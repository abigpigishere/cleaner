#define NOMINMAX
#include <windows.h>
#include <winioctl.h>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

using Clock = std::chrono::steady_clock;
using Bytes = std::vector<uint8_t>;
constexpr uint64_t Mask48 = (1ull << 48) - 1, UnknownSize = UINT64_MAX;
constexpr uint32_t NoName = UINT32_MAX;
enum Status : uint8_t { Empty, Parsed, Directory, Extension, Unsupported, Incomplete, Error, Unused };
#pragma pack(push, 1)
struct Node {
    uint64_t reference{}, parent{}, logical{UnknownSize}, allocated{UnknownSize}, treeLogical{}, treeAllocated{};
    uint32_t nameBegin{}, nameCount{}, streamBegin{}, streamCount{};
    uint16_t links{};
    uint8_t status{}, flags{};
    uint32_t chosenName{NoName};
    uint64_t unknownDescendants{};
};
struct Name { uint64_t parent; uint32_t text; uint16_t chars; uint8_t space, reserved; };
struct Stream {
    uint64_t logical{}, allocated{}, headerAllocated{}, runAllocated{}, initialized{}, low{}, high{};
    uint32_t text{};
    uint16_t chars{}, flags{}, instance{};
    uint8_t resident{}, kind{};
};
struct Header {
    char magic[8]; uint32_t version, nodeBytes;
    uint64_t nodes, names, streams, textBytes;
};
#pragma pack(pop)
static_assert(sizeof(Node) == 80 && sizeof(Name) == 16 && sizeof(Stream) == 68 && sizeof(Header) == 48);
template<class T> T load(const uint8_t* p) { T v; std::memcpy(&v, p, sizeof(v)); return v; }
template<class T> void put(uint8_t* p, T v) { std::memcpy(p, &v, sizeof(v)); }
double seconds(Clock::time_point t) { return std::chrono::duration<double>(Clock::now()-t).count(); }
void require(bool ok, const char* message) { if (!ok) throw std::runtime_error(message); }
struct Handle {
    HANDLE h{INVALID_HANDLE_VALUE};
    ~Handle() { if (h != INVALID_HANDLE_VALUE) CloseHandle(h); }
};
struct Pool {
    std::vector<Name> names; std::vector<Stream> streams; Bytes text;
    uint32_t addText(const uint8_t* p, size_t length) {
        require(text.size()+length < UINT32_MAX, "Name pool exceeds format limit");
        auto offset = static_cast<uint32_t>(text.size()); text.insert(text.end(),p,p+length); return offset;
    }
};

// Strict on-disk USA fixups. IOCTL-normalized records never enter this parser.
void fixup(uint8_t* p, size_t length) {
    require(length >= 48 && load<uint32_t>(p) == 0x454c4946, "Invalid FILE signature");
    uint16_t offset=load<uint16_t>(p+4), count=load<uint16_t>(p+6);
    require(length % 512 == 0 && count == length/512+1 && offset >= 8 &&
            size_t(offset)+count*2 <= 510, "Invalid USA bounds");
    uint16_t marker=load<uint16_t>(p+offset);
    for (size_t i=1; i<count; ++i) require(load<uint16_t>(p+i*512-2)==marker,"USA mismatch");
    for (size_t i=1; i<count; ++i) put<uint16_t>(p+i*512-2,load<uint16_t>(p+offset+i*2));
}

uint64_t allocatedRuns(const uint8_t* a, size_t length, size_t offset, uint64_t low, uint64_t high) {
    require(offset < length,"Run offset out of bounds");
    if (low==0 && high==UnknownSize) { require(a[offset]==0,"Nonempty empty run list"); return 0; }
    require(high>=low && high!=UnknownSize,"Invalid VCN range");
    uint64_t vcn=low, total=0; int64_t lcn=0;
    while (offset < length) {
        uint8_t tag=a[offset++];
        if (!tag) { require(vcn==high+1,"Run coverage mismatch"); return total; }
        unsigned width=tag&15, deltaWidth=tag>>4;
        require(width>=1 && width<=8 && deltaWidth<=8 && offset+width+deltaWidth<=length,"Invalid run widths");
        uint64_t count=0, delta=0;
        std::memcpy(&count,a+offset,width); offset+=width;
        require(count>0 && count<=high+1-vcn,"Invalid run span");
        if (deltaWidth) {
            std::memcpy(&delta,a+offset,deltaWidth);
            if (deltaWidth<8 && (a[offset+deltaWidth-1]&128)) delta |= ~0ull << (deltaWidth*8);
            int64_t signedDelta; std::memcpy(&signedDelta,&delta,8);
            require(!((signedDelta>0 && lcn>INT64_MAX-signedDelta) ||
                      (signedDelta<0 && lcn<INT64_MIN-signedDelta)),"LCN overflow");
            lcn+=signedDelta; require(lcn>=0,"Negative LCN");
            require(total<=UINT64_MAX-count,"Cluster count overflow"); total+=count;
        }
        offset+=deltaWidth; vcn+=count;
    }
    throw std::runtime_error("Unterminated run list");
}

void parse(uint8_t* p, size_t size, uint64_t number, uint32_t cluster, Node& n, Pool& pool) {
    if (load<uint32_t>(p)==0) { n.status=Empty; return; }
    require(load<uint32_t>(p)==0x454c4946,"Invalid FILE signature");
    uint16_t flags=load<uint16_t>(p+22);
    if (!(flags&1)) { n.status=Unused; return; }
    fixup(p,size);
    // Extension records keep their attributes here; they are merged into the base record after the scan.
    uint64_t baseReference=load<uint64_t>(p+32);
    size_t position=load<uint16_t>(p+20); uint32_t used=load<uint32_t>(p+24);
    require(load<uint32_t>(p+28)==size && position>=48 && position%8==0 && position<used && used<=size,
            "Invalid attribute record bounds");
    n.reference=number | uint64_t(load<uint16_t>(p+16))<<48;
    n.links=load<uint16_t>(p+18); n.flags=uint8_t(flags&2 ? 1:0);
    require(pool.names.size()<UINT32_MAX && pool.streams.size()<UINT32_MAX,"Pool count overflow");
    n.nameBegin=static_cast<uint32_t>(pool.names.size()); n.streamBegin=static_cast<uint32_t>(pool.streams.size());
    bool attributeList=false, terminator=false; uint32_t mainCount=0; Stream main{};
    while (position+4<=used) {
        const uint8_t* a=p+position; uint32_t type=load<uint32_t>(a);
        if (type==0xffffffff) { terminator=true; break; }
        require(position+24<=used,"Truncated attribute header");
        uint32_t length=load<uint32_t>(a+4); uint8_t form=a[8], chars=a[9]; uint16_t nameOffset=load<uint16_t>(a+10);
        require(length>=24 && length%8==0 && length<=used-position && form<=1,"Invalid attribute length/form");
        uint32_t headerSize=form ? 64:24;
        require(length>=headerSize && (!chars || (nameOffset>=headerSize && nameOffset%2==0 && size_t(nameOffset)+chars*2<=length)),
                "Invalid attribute name bounds");
        uint32_t valueLength=0; uint16_t valueOffset=0;
        if (!form) {
            valueLength=load<uint32_t>(a+16); valueOffset=load<uint16_t>(a+20);
            require(valueOffset>=24 && uint64_t(valueOffset)+valueLength<=length,"Invalid resident bounds");
        }
        if(form) {
            const uint64_t low=load<uint64_t>(a+16),high=load<uint64_t>(a+24);
            const uint16_t offset=load<uint16_t>(a+32),af=load<uint16_t>(a+12);
            require(offset>=((low==0 && (af&0x8001))?72:64) && offset<length,"Invalid allocation run header");
            uint64_t clusters=allocatedRuns(a,length,offset,low,high);
            require(clusters<=UINT64_MAX/cluster && n.treeAllocated<=UINT64_MAX-clusters*cluster,"Attribute allocation overflow");
            n.treeAllocated+=clusters*cluster;
        }
        if (type==0x20) attributeList=true;
        if (type==0x30 && !form) {
            require(valueLength>=66,"Truncated FILE_NAME");
            const uint8_t* v=a+valueOffset; uint8_t nc=v[64], space=v[65];
            require(uint32_t(66+nc*2)<=valueLength,"Invalid FILE_NAME length");
            Name name{load<uint64_t>(v),pool.addText(v+66,nc*2),nc,space,0};
            uint32_t index=static_cast<uint32_t>(pool.names.size()); pool.names.push_back(name); ++n.nameCount;
            if (n.chosenName==NoName || (pool.names[n.chosenName].space==2 && space!=2)) {
                n.chosenName=index; n.parent=name.parent;
            }
        }
        if (type==0x80) {
            Stream s{}; s.chars=chars; s.text=chars ? pool.addText(a+nameOffset,chars*2) : 0;
            s.flags=load<uint16_t>(a+12); s.instance=load<uint16_t>(a+14); s.resident=uint8_t(!form);
            if (!form) { s.logical=valueLength; s.kind=0; }
            else {
                s.low=load<uint64_t>(a+16); s.high=load<uint64_t>(a+24);
                uint16_t runOffset=load<uint16_t>(a+32); bool extended=s.low==0 && (s.flags&0x8001)!=0;
                s.headerAllocated=load<uint64_t>(a+40); s.logical=load<uint64_t>(a+48); s.initialized=load<uint64_t>(a+56);
                require(runOffset>=(extended ? 72:64) && runOffset<length && (s.low!=0 || s.initialized<=s.logical),"Invalid nonresident bounds");
                if (s.low==0 && s.high==UnknownSize) require(!s.logical && !s.headerAllocated && !s.initialized,"Empty run with nonempty data");
                s.allocated=extended ? load<uint64_t>(a+64):s.headerAllocated;
                uint64_t clusters=allocatedRuns(a,length,runOffset,s.low,s.high);
                require(clusters<=UINT64_MAX/cluster,"Run byte count overflow"); s.runAllocated=clusters*cluster;
                s.kind=uint8_t(s.flags&0x8000 ? 2:(s.flags&1 ? 3:1));
                if (s.low) { s.logical=UnknownSize; s.allocated=UnknownSize; }
                else if (s.allocated!=s.runAllocated) s.kind|=128;
            }
            pool.streams.push_back(s); ++n.streamCount;
            if (!chars) { ++mainCount; main=s; }
        }
        position+=length;
    }
    require(terminator,"Missing attribute terminator");
    if (baseReference) { n.status=Extension; n.parent=baseReference; n.chosenName=NoName; return; }
    if (n.flags&1) n.status=Directory;
    else if (attributeList) n.status=Unsupported;
    else if (mainCount!=1 || main.low || (main.kind&128)) n.status=Incomplete;
    else { n.status=Parsed; n.logical=main.logical; n.allocated=main.allocated; if (main.resident) n.flags|=2; }
}

// Unnamed DATA split across ATTRIBUTE_LIST extension records: the VCN-0 segment carries the sizes,
// the other segments must cover the VCN range contiguously and their runs must add up to the allocation.
bool resolveSegments(Node& n,const std::vector<Stream>& streams,uint32_t cluster,bool& mismatch) {
    const Stream* first=nullptr; uint32_t segments=0; uint64_t runs=0;
    std::vector<std::pair<uint64_t,uint64_t>> spans;
    for(uint32_t k=n.streamBegin;k<n.streamBegin+n.streamCount;++k) {
        const auto& s=streams[k]; if(s.chars) continue;
        ++segments; if(s.resident || s.low==0) { if(first) return false; first=&s; }
        if(!s.resident) { spans.push_back({s.low,s.high}); runs+=s.runAllocated; }
    }
    if(!first) return false;
    if(first->resident) {
        if(segments!=1) return false;
        n.logical=first->logical; n.allocated=0; n.flags|=2; return true;
    }
    std::sort(spans.begin(),spans.end());
    if(first->high==UnknownSize) { if(segments!=1) return false; n.logical=0; n.allocated=0; return true; }
    for(size_t j=1;j<spans.size();++j) if(spans[j-1].second==UnknownSize || spans[j].first!=spans[j-1].second+1) return false;
    if(spans.back().second==UnknownSize || (spans.back().second+1)*cluster!=first->headerAllocated) return false;
    if(runs!=first->allocated) { mismatch=true; return false; }
    n.logical=first->logical; n.allocated=first->allocated; return true;
}

template<class Base>
void mergeExtensions(std::vector<Node>& nodes,std::vector<Name>& names,std::vector<Stream>& streams,uint32_t cluster,Base base,
                     uint64_t& stray,uint64_t& merged,uint64_t& mismatches) {
    std::vector<uint32_t> start(nodes.size()+1),list;
    for(size_t i=0;i<nodes.size();++i) if(nodes[i].status==Extension) {
        uint64_t b=nodes[i].parent&Mask48;
        if(b<nodes.size() && base(nodes[b].status) && nodes[b].reference==nodes[i].parent) ++start[b+1]; else ++stray;
    }
    for(size_t i=0;i<nodes.size();++i) start[i+1]+=start[i];
    list.resize(start.back()); { auto fill=start;
        for(size_t i=0;i<nodes.size();++i) if(nodes[i].status==Extension) {
            uint64_t b=nodes[i].parent&Mask48;
            if(b<nodes.size() && base(nodes[b].status) && nodes[b].reference==nodes[i].parent) list[fill[b]++]=static_cast<uint32_t>(i);
        } }
    std::vector<Name> outNames; std::vector<Stream> outStreams; outNames.reserve(names.size()); outStreams.reserve(streams.size());
    for(size_t i=0;i<nodes.size();++i) {
        auto& n=nodes[i];
        if(!base(n.status)) continue; // Extension ranges must survive until every base has copied them.
        uint32_t nb=static_cast<uint32_t>(outNames.size()),sb=static_cast<uint32_t>(outStreams.size());
        auto append=[&](const Node& from) {
            outNames.insert(outNames.end(),names.begin()+from.nameBegin,names.begin()+from.nameBegin+from.nameCount);
            outStreams.insert(outStreams.end(),streams.begin()+from.streamBegin,streams.begin()+from.streamBegin+from.streamCount);
        };
        append(n); for(uint32_t k=start[i];k<start[i+1];++k) {
            const auto& extension=nodes[list[k]]; append(extension);
            require(n.treeAllocated<=UINT64_MAX-extension.treeAllocated,"Extension allocation overflow");
            n.treeAllocated+=extension.treeAllocated;
        }
        n.nameBegin=nb; n.nameCount=static_cast<uint32_t>(outNames.size())-nb;
        n.streamBegin=sb; n.streamCount=static_cast<uint32_t>(outStreams.size())-sb;
        n.chosenName=NoName;
        for(uint32_t k=nb;k<nb+n.nameCount;++k)
            if(n.chosenName==NoName || (outNames[n.chosenName].space==2 && outNames[k].space!=2)) { n.chosenName=k; n.parent=outNames[k].parent; }
        if((n.status==Unsupported || n.status==Incomplete) ) {
            bool mismatch=false;
            if(resolveSegments(n,outStreams,cluster,mismatch)) { n.status=Parsed; ++merged; }
            else if(mismatch) ++mismatches;
        }
    }
    // An internal index-only file or named-stream-only file legitimately has no unnamed DATA.
    // All its nonresident attributes have still been strictly parsed and counted above.
    for(auto& n:nodes) if(base(n.status)) {
        bool unnamed=false;
        for(uint32_t k=n.streamBegin;k<n.streamBegin+n.streamCount;++k) if(outStreams[k].chars==0) unnamed=true;
        if(n.status!=Directory && !unnamed && (n.streamCount>0 || (n.reference&Mask48)<32)) { n.logical=0; n.status=Parsed; }
        if(n.status==Directory) { n.logical=0; n.allocated=n.treeAllocated; }
        else if(n.status==Parsed) n.allocated=n.treeAllocated;
    }
    for(auto& n:nodes) if(!base(n.status)) n.nameBegin=n.nameCount=n.streamBegin=n.streamCount=0;
    names.swap(outNames); streams.swap(outStreams);
}

struct Run { uint64_t vcn, lcn, count; };
struct Job { uint64_t first; Bytes bytes; };
struct Queue {
    std::mutex mutex; std::condition_variable changed; std::deque<Job> jobs;
    std::deque<Bytes> buffers;
    size_t capacity; bool closed=false; std::atomic<bool> failed{false};
    explicit Queue(size_t c):capacity(c) {
        for(size_t i=0;i<c+2;++i) buffers.emplace_back(8*1024*1024);
    }
    Bytes acquire(size_t length) {
        std::unique_lock lock(mutex); changed.wait(lock,[&]{return !buffers.empty() || failed.load();});
        require(!failed,"Worker failed"); Bytes buffer=std::move(buffers.front()); buffers.pop_front();
        buffer.resize(length); return buffer;
    }
    void recycle(Bytes buffer) {
        std::lock_guard lock(mutex); buffers.push_back(std::move(buffer)); changed.notify_all();
    }
    void push(Job job) {
        std::unique_lock lock(mutex); changed.wait(lock,[&]{return jobs.size()<capacity || failed.load();});
        require(!failed,"Worker failed"); jobs.push_back(std::move(job)); changed.notify_all();
    }
    bool pop(Job& job) {
        std::unique_lock lock(mutex); changed.wait(lock,[&]{return !jobs.empty() || closed || failed.load();});
        if (failed || jobs.empty()) return false;
        job=std::move(jobs.front()); jobs.pop_front(); changed.notify_all(); return true;
    }
    void close() { std::lock_guard lock(mutex); closed=true; changed.notify_all(); }
};

struct Arguments {
    std::wstring drive, output;
    uint32_t record=0, cluster=0, sector=0, workers=4;
    uint64_t valid=0, volumeBytes=0, serial=0;
    std::vector<Run> runs;
};
Arguments arguments(int argc,wchar_t** argv) {
    Arguments a;
    for (int i=1;i<argc;++i) {
        std::wstring key=argv[i]; require(i+1<argc,"Missing argument value"); std::wstring value=argv[++i];
        if (key==L"--drive") a.drive=value;
        else if (key==L"--output-dir") a.output=value;
        else if (key==L"--record") a.record=static_cast<uint32_t>(std::stoul(value));
        else if (key==L"--cluster") a.cluster=static_cast<uint32_t>(std::stoul(value));
        else if (key==L"--sector") a.sector=static_cast<uint32_t>(std::stoul(value));
        else if (key==L"--workers") a.workers=static_cast<uint32_t>(std::stoul(value));
        else if (key==L"--valid") a.valid=std::stoull(value);
        else if (key==L"--volume-bytes") a.volumeBytes=std::stoull(value);
        else if (key==L"--serial") a.serial=std::stoull(value,nullptr,0);
        else if (key==L"--run") {
            std::replace(value.begin(),value.end(),L',',L' '); std::wistringstream input(value); Run r{};
            require(bool(input>>r.vcn>>r.lcn>>r.count),"Invalid run argument"); a.runs.push_back(r);
        } else throw std::runtime_error("Unknown argument");
    }
    require(a.drive.size()==2 && a.drive[0]>=L'A' && a.drive[0]<=L'Z' && a.drive[1]==L':',"Expected local drive letter");
    require(!a.output.empty() && a.workers>=1 && a.workers<=16 && a.record>=512 && a.record<=65536 && !(a.record&(a.record-1)),"Invalid scan settings");
    require(a.sector>=512 && !(a.sector&(a.sector-1)) && a.cluster>=a.sector && !(a.cluster&(a.cluster-1)),"Invalid volume geometry");
    require(a.valid>0 && a.valid%a.record==0 && a.valid/a.record<=UINT32_MAX,"Invalid MFT length");
    require(!a.runs.empty() && a.runs.size()<=4096,"Invalid MFT run count");
    uint64_t next=0;
    for (auto r:a.runs) {
        require(r.vcn==next && r.count>0 && r.lcn<a.volumeBytes/a.cluster && r.count<=a.volumeBytes/a.cluster-r.lcn,"MFT map outside volume or has holes");
        require(next<=UINT64_MAX-r.count,"MFT map overflow"); next+=r.count;
    }
    require(next<=UINT64_MAX/a.cluster && next*a.cluster>=a.valid,"MFT map lacks coverage");
    return a;
}

void writeBuffer(std::ofstream& out,const void* data,size_t bytes) {
    if (bytes) out.write(static_cast<const char*>(data),static_cast<std::streamsize>(bytes));
    require(bool(out),"Index write failed");
}
std::string quote(const std::string& s) {
    std::ostringstream out; out<<'"';
    for (unsigned char c:s) { if (c=='"' || c=='\\') out<<'\\'<<char(c); else if(c<32) out<<'?'; else out<<char(c); }
    out<<'"'; return out.str();
}

int scan(const Arguments& a,Clock::time_point processStart) {
    namespace fs=std::filesystem;
    fs::path output(a.output); require(fs::create_directory(output),"Output directory must be new");
    auto progress=[&](const char* phase,uint64_t done,uint64_t total) {
        std::ofstream file(output/L"native-progress.json",std::ios::trunc);
        if(file) file<<"{\"phase\":\""<<phase<<"\",\"processed_slots\":"<<done
                     <<",\"total_slots\":"<<total<<",\"percent\":"
                     <<(total ? 100.0*double(done)/double(total):0.0)
                     <<",\"native_elapsed_seconds\":"<<seconds(processStart)<<"}";
    };
    progress("initializing",0,0);
    Handle volume; volume.h=CreateFileW((L"\\\\.\\"+a.drive).c_str(),GENERIC_READ,7,nullptr,OPEN_EXISTING,0,nullptr);
    require(volume.h!=INVALID_HANDLE_VALUE,"Cannot open volume read-only (administrator required)");
    auto info=[&] {
        NTFS_VOLUME_DATA_BUFFER v{}; DWORD returned=0;
        require(DeviceIoControl(volume.h,FSCTL_GET_NTFS_VOLUME_DATA,nullptr,0,&v,sizeof(v),&returned,nullptr)!=0,"Volume metadata query failed");
        require(returned>=sizeof(v),"Truncated volume metadata"); return v;
    };
    auto before=info();
    require(before.BytesPerSector==a.sector && before.BytesPerCluster==a.cluster && before.BytesPerFileRecordSegment==a.record &&
            uint64_t(before.VolumeSerialNumber.QuadPart)==a.serial && uint64_t(before.MftValidDataLength.QuadPart)==a.valid &&
            uint64_t(before.MftStartLcn.QuadPart)==a.runs[0].lcn,"Volume changed after bootstrap");
    uint64_t slots=a.valid/a.record;
    progress("scanning",0,slots);
    std::vector<Node> nodes(static_cast<size_t>(slots));
    std::vector<uint8_t> owner(static_cast<size_t>(slots));
    std::vector<Pool> pools(a.workers);
    std::vector<double> workerTimes(a.workers);
    std::vector<std::string> errors; std::mutex errorMutex;
    Queue queue(a.workers);
    std::atomic<uint64_t> processedSlots{0};
    std::vector<std::thread> workers;
    for (uint32_t worker=0;worker<a.workers;++worker) workers.emplace_back([&,worker] {
        try {
            Job job;
            while(queue.pop(job)) {
                auto start=Clock::now(); auto& pool=pools[worker];
                for (size_t within=0;within<job.bytes.size();within+=a.record) {
                    uint64_t number=job.first+within/a.record; auto& n=nodes[static_cast<size_t>(number)];
                    owner[static_cast<size_t>(number)]=static_cast<uint8_t>(worker);
                    try { parse(job.bytes.data()+within,a.record,number,a.cluster,n,pool); }
                    catch(const std::exception& e) {
                        n=Node{}; n.status=Error;
                        std::lock_guard lock(errorMutex);
                        if (errors.size()<200) errors.push_back(std::to_string(number)+": "+e.what());
                    }
                }
                workerTimes[worker]+=seconds(start);
                processedSlots.fetch_add(job.bytes.size()/a.record,std::memory_order_relaxed);
                queue.recycle(std::move(job.bytes));
            }
        } catch(...) { queue.failed=true; queue.changed.notify_all(); }
    });
    uint64_t readCalls=0,readBytes=0; double ioSeconds=0;
    std::string producerError;
    auto lastProgress=Clock::now();
    try {
        constexpr uint64_t blockSize=8*1024*1024;
        size_t runIndex=0;
        for (uint64_t offset=0;offset<a.valid;offset+=blockSize) {
            uint64_t length=std::min(blockSize,a.valid-offset); Job job{offset/a.record,queue.acquire(static_cast<size_t>(length))};
            uint64_t within=0;
            while(within<length) {
                uint64_t logical=offset+within;
                while(runIndex+1<a.runs.size() && logical>=a.runs[runIndex+1].vcn*a.cluster) ++runIndex;
                auto r=a.runs[runIndex]; uint64_t relative=logical-r.vcn*a.cluster;
                uint64_t take=std::min(length-within,r.count*a.cluster-relative), physical=r.lcn*a.cluster+relative;
                uint64_t aligned=physical/a.sector*a.sector, skip=physical-aligned;
                DWORD requested=static_cast<DWORD>((skip+take+a.sector-1)/a.sector*a.sector);
                Bytes bounce; uint8_t* destination=job.bytes.data()+within;
                if(skip || requested!=take) { bounce.resize(requested); destination=bounce.data(); }
                LARGE_INTEGER position{}; position.QuadPart=static_cast<LONGLONG>(aligned); DWORD received=0;
                auto start=Clock::now();
                require(SetFilePointerEx(volume.h,position,nullptr,FILE_BEGIN)!=0,"Raw seek failed");
                require(ReadFile(volume.h,destination,requested,&received,nullptr)!=0 && received==requested,"Raw read failed or short");
                ioSeconds+=seconds(start); ++readCalls; readBytes+=received;
                if(!bounce.empty()) std::memcpy(job.bytes.data()+within,bounce.data()+skip,static_cast<size_t>(take));
                within+=take;
            }
            queue.push(std::move(job));
            if(seconds(lastProgress)>=0.08) {
                progress("scanning",processedSlots.load(std::memory_order_relaxed),slots);
                lastProgress=Clock::now();
            }
        }
    } catch(const std::exception& e) { producerError=e.what(); queue.failed=true; queue.changed.notify_all(); }
    queue.close(); for (auto& worker:workers) worker.join();
    require(producerError.empty(),producerError.c_str()); require(!queue.failed,"Worker pipeline failed");
    double pipelineSeconds=seconds(processStart);
    progress("merging",slots,slots);

    // Consolidate compact per-worker arenas; no per-file JSON or object graphs.
    auto mergeStart=Clock::now();
    std::vector<Name> names; std::vector<Stream> streams; Bytes text;
    size_t totalNames=0,totalStreams=0,totalText=0;
    for (const auto& p:pools) { totalNames+=p.names.size(); totalStreams+=p.streams.size(); totalText+=p.text.size(); }
    require(totalNames<UINT32_MAX && totalStreams<UINT32_MAX && totalText<UINT32_MAX,"Index arena exceeds format limit");
    names.reserve(totalNames); streams.reserve(totalStreams); text.reserve(totalText);
    std::vector<uint32_t> nameBase(a.workers),streamBase(a.workers);
    for (uint32_t worker=0;worker<a.workers;++worker) {
        auto& p=pools[worker]; nameBase[worker]=static_cast<uint32_t>(names.size()); streamBase[worker]=static_cast<uint32_t>(streams.size());
        auto textBase=static_cast<uint32_t>(text.size());
        for(auto n:p.names) { n.text+=textBase; names.push_back(n); }
        for(auto s:p.streams) { s.text+=textBase; streams.push_back(s); }
        text.insert(text.end(),p.text.begin(),p.text.end());
        p=Pool{};
    }
    auto base=[](uint8_t status) { return status==Parsed || status==Directory || status==Unsupported || status==Incomplete; };
    for(size_t i=0;i<nodes.size();++i) {
        auto& n=nodes[i]; if(!base(n.status) && n.status!=Extension) continue;
        auto worker=owner[i]; n.nameBegin+=nameBase[worker]; n.streamBegin+=streamBase[worker];
    }
    uint64_t strayExtensions=0,mergedSizes=0,mergeMismatches=0;
    mergeExtensions(nodes,names,streams,a.cluster,base,strayExtensions,mergedSizes,mergeMismatches);
    uint64_t counts[8]{}; uint64_t files=0,dirs=0,logicalTotal=0,allocatedTotal=0;
    for(size_t i=0;i<nodes.size();++i) {
        auto& n=nodes[i]; ++counts[n.status];
        if(!base(n.status)) continue;
        if(n.status==Directory) { ++dirs; n.treeLogical=0; } else ++files;
        if(n.status==Parsed) { logicalTotal+=n.logical; allocatedTotal+=n.allocated; n.treeLogical=n.logical; n.treeAllocated=n.allocated; }
        else if(n.status!=Directory) n.unknownDescendants=1;
    }
    double mergeSeconds=seconds(mergeStart);
    progress("aggregating",slots,slots);
    // One canonical long-name parent per file; all link names are retained.
    auto treeStart=Clock::now(); std::vector<uint32_t> parent(nodes.size(),UINT32_MAX),children(nodes.size());
    std::vector<uint32_t> leaves; leaves.reserve(nodes.size()); uint64_t orphans=0,active=0;
    for(uint32_t i=0;i<nodes.size();++i) {
        auto& n=nodes[i]; if(!base(n.status)) continue; ++active;
        uint64_t candidate=n.parent&Mask48;
        if(candidate==i && i==5) continue;
        if(n.chosenName!=NoName && candidate<nodes.size() && nodes[static_cast<size_t>(candidate)].status==Directory &&
           nodes[static_cast<size_t>(candidate)].reference==n.parent && candidate!=i) {
            parent[i]=static_cast<uint32_t>(candidate); ++children[static_cast<size_t>(candidate)];
        } else ++orphans;
    }
    for(uint32_t i=0;i<nodes.size();++i) if(base(nodes[i].status) && children[i]==0) leaves.push_back(i);
    for(size_t i=0;i<leaves.size();++i) {
        uint32_t child=leaves[i], p=parent[child]; if(p==UINT32_MAX) continue;
        auto& destination=nodes[p]; auto& source=nodes[child];
        require(destination.treeLogical<=UINT64_MAX-source.treeLogical && destination.treeAllocated<=UINT64_MAX-source.treeAllocated,
                "Directory size overflow");
        destination.treeLogical+=source.treeLogical; destination.treeAllocated+=source.treeAllocated;
        destination.unknownDescendants+=source.unknownDescendants;
        if(--children[p]==0) leaves.push_back(p);
    }
    uint64_t cycleNodes=active-leaves.size(); double treeSeconds=seconds(treeStart);
    auto after=info();
    bool stable=before.MftValidDataLength.QuadPart==after.MftValidDataLength.QuadPart && before.MftStartLcn.QuadPart==after.MftStartLcn.QuadPart;
    double readySeconds=seconds(processStart);
    progress("saving",slots,slots);
    auto writeStart=Clock::now();
    std::ofstream index(output/L"index.bin",std::ios::binary|std::ios::trunc); require(bool(index),"Cannot create binary index");
    Header header{{'C','L','N','I','D','X','1',0},1,sizeof(Node),nodes.size(),names.size(),streams.size(),text.size()};
    writeBuffer(index,&header,sizeof(header)); writeBuffer(index,nodes.data(),nodes.size()*sizeof(Node));
    writeBuffer(index,names.data(),names.size()*sizeof(Name)); writeBuffer(index,streams.data(),streams.size()*sizeof(Stream));
    writeBuffer(index,text.data(),text.size()); index.flush(); require(bool(index),"Index flush failed"); index.close();
    double writeSeconds=seconds(writeStart),totalSeconds=seconds(processStart),workerCpu=0;
    for(auto t:workerTimes) workerCpu+=t;
    std::ofstream report(output/L"native-report.json"); report<<std::fixed<<std::setprecision(6);
    report<<"{\n\"status\":\""<<(counts[Error] ? "completed_with_errors":"completed")<<"\",\n"
          <<"\"allocation_model\":\"all_nonresident_attributes_unique_record\",\n\"scan_ready_seconds\":"<<readySeconds<<",\n\"total_seconds\":"<<totalSeconds
          <<",\n\"pipeline_seconds\":"<<pipelineSeconds<<",\n\"read_call_seconds\":"<<ioSeconds
          <<",\n\"worker_seconds_sum\":"<<workerCpu<<",\n\"arena_merge_seconds\":"<<mergeSeconds
          <<",\n\"directory_aggregation_seconds\":"<<treeSeconds<<",\n\"index_write_seconds\":"<<writeSeconds
          <<",\n\"workers\":"<<a.workers<<",\n\"slots\":"<<slots<<",\n\"files\":"<<files<<",\n\"directories\":"<<dirs
          <<",\n\"sizes_parsed\":"<<counts[Parsed]<<",\n\"sizes_unresolved\":"<<counts[Unsupported]+counts[Incomplete]
          <<",\n\"empty\":"<<counts[Empty]<<",\n\"unused\":"<<counts[Unused]<<",\n\"extensions\":"<<counts[Extension]
          <<",\n\"errors\":"<<counts[Error]<<",\n\"raw_read_calls\":"<<readCalls<<",\n\"raw_bytes\":"<<readBytes
          <<",\n\"named_records\":"<<names.size()<<",\n\"streams\":"<<streams.size()<<",\n\"known_main_logical_bytes\":"<<logicalTotal
          <<",\n\"known_main_allocated_bytes\":"<<allocatedTotal<<",\n\"attribute_list_sizes_merged\":"<<mergedSizes
          <<",\n\"attribute_list_run_mismatches\":"<<mergeMismatches<<",\n\"stray_extensions\":"<<strayExtensions
          <<",\n\"orphan_nodes\":"<<orphans
          <<",\n\"cycle_nodes\":"<<cycleNodes<<",\n\"volume_length_and_start_unchanged\":"<<(stable?"true":"false")
          <<",\n\"error_sample\":[";
    for(size_t i=0;i<errors.size();++i) { if(i) report<<','; report<<quote(errors[i]); }
    report<<"]\n}\n"; require(bool(report),"Report write failed");
    progress("done",slots,slots);
    std::cout<<"ready="<<readySeconds<<" total="<<totalSeconds<<" files="<<files<<" errors="<<counts[Error]<<'\n';
    return counts[Error] || cycleNodes || !stable ? 1:0;
}

Bytes synthetic(bool resident=true,bool sparse=false) {
    Bytes r(1024); put<uint32_t>(r.data(),0x454c4946); put<uint16_t>(r.data()+4,48); put<uint16_t>(r.data()+6,3);
    put<uint16_t>(r.data()+16,7); put<uint16_t>(r.data()+18,1); put<uint16_t>(r.data()+20,56); put<uint16_t>(r.data()+22,1);
    put<uint32_t>(r.data()+28,1024); uint8_t* a=r.data()+56; put<uint32_t>(a,0x80);
    if(resident) { put<uint32_t>(a+4,152); put<uint32_t>(a+16,128); put<uint16_t>(a+20,24); }
    else {
        a[8]=1; put<uint32_t>(a+4,80); put<uint16_t>(a+12,sparse ? 0x8000:0);
        put<uint64_t>(a+16,0); put<uint64_t>(a+24,sparse ? 3:1); put<uint16_t>(a+32,sparse ? 72:64);
        put<uint64_t>(a+40,sparse ? 16384:8192); put<uint64_t>(a+48,sparse ? 15000:8191);
        if(sparse) { put<uint64_t>(a+64,4096); const uint8_t runs[]={0x11,1,8,1,3,0}; std::memcpy(a+72,runs,6); }
        else { const uint8_t runs[]={0x11,2,8,0}; std::memcpy(a+64,runs,4); }
    }
    uint32_t end=56+load<uint32_t>(a+4); put<uint32_t>(r.data()+end,0xffffffff); put<uint32_t>(r.data()+24,end+4);
    put<uint16_t>(r.data()+48,0xbbaa); put<uint16_t>(r.data()+510,0xbbaa); put<uint16_t>(r.data()+1022,0xbbaa);
    return r;
}
int selfTest() {
    unsigned count=0;
    auto check=[&](Bytes r,uint64_t logical,uint64_t allocated) { Node n; Pool p; parse(r.data(),r.size(),12,4096,n,p);
        require(n.status==Parsed && n.logical==logical && n.allocated==allocated,"Size self-test failed"); ++count; };
    auto reject=[&](Bytes r) { Node n; Pool p; bool rejected=false; try { parse(r.data(),r.size(),12,4096,n,p); } catch(const std::exception&) { rejected=true; }
        require(rejected,"Corruption accepted"); ++count; };
    check(synthetic(),128,0); check(synthetic(false),8191,8192); check(synthetic(false,true),15000,4096);
    auto r=synthetic(false); auto* a=r.data()+56; put<uint64_t>(a+24,UnknownSize); put<uint64_t>(a+40,0); put<uint64_t>(a+48,0); a[64]=0;
    check(r,0,0); put<uint64_t>(r.data()+56+48,1); reject(r);
    r=synthetic(); r[510]=0; reject(r);
    r=synthetic(); put<uint32_t>(r.data()+60,4096); reject(r);
    r=synthetic(false); r[56+65]=3; reject(r);
    r=synthetic(); put<uint16_t>(r.data()+56+20,900); reject(r);
    r=synthetic(); put<uint16_t>(r.data()+6,4); reject(r);
    r=synthetic(); put<uint32_t>(r.data()+208,0x81); reject(r);
    // Compressed continuation extents have a 64-byte header; sizes only apply at VCN 0.
    r=synthetic(false); a=r.data()+56; put<uint64_t>(r.data()+32,42);
    put<uint16_t>(a+12,1); put<uint64_t>(a+16,2); put<uint64_t>(a+24,3);
    put<uint64_t>(a+48,0); put<uint64_t>(a+56,999);
    { Node n; Pool p; auto continuation=r; parse(continuation.data(),continuation.size(),12,4096,n,p);
      require(n.status==Extension && p.streams.size()==1 && p.streams[0].logical==UnknownSize &&
              p.streams[0].runAllocated==8192,"Compressed continuation self-test failed"); ++count; }
    r[56+65]=3; reject(r);
    r=synthetic(false); put<uint64_t>(r.data()+56+56,99999); reject(r);
    auto segments=[&](uint64_t secondRuns,uint64_t secondLow,bool expected) {
        std::vector<Stream> s(2); s[0].low=0; s[0].high=9; s[0].headerAllocated=s[0].allocated=20*4096; s[0].logical=79000; s[0].runAllocated=10*4096;
        s[1].low=secondLow; s[1].high=19; s[1].logical=s[1].allocated=UnknownSize; s[1].runAllocated=secondRuns;
        Node n; n.streamBegin=0; n.streamCount=2; bool mismatch=false;
        require(resolveSegments(n,s,4096,mismatch)==expected && (!expected || (n.logical==79000 && n.allocated==20*4096)),"Segment merge self-test failed"); ++count; };
    segments(10*4096,10,true); segments(9*4096,10,false); segments(10*4096,11,false);
    { std::vector<Node> ns(3); ns[1].status=Extension; ns[1].parent=2; ns[1].streamCount=1;
      ns[2].status=Unsupported; ns[2].reference=2;
      std::vector<Name> names; std::vector<Stream> ss(1); ss[0].resident=1; ss[0].logical=123;
      uint64_t stray=0,merged=0,mismatch=0;
      mergeExtensions(ns,names,ss,4096,[](uint8_t s){return s==Parsed||s==Unsupported;},stray,merged,mismatch);
      require(ns[2].status==Parsed && ns[2].logical==123 && merged==1,"Earlier extension lost"); ++count; }
    { auto indexRecord=synthetic(false); put<uint32_t>(indexRecord.data()+56,0xa0); Node n; Pool pool;
      parse(indexRecord.data(),indexRecord.size(),12,4096,n,pool);
      require(n.treeAllocated==8192,"Index allocation omitted"); ++count; }
    std::cout<<"Native parser self-tests passed: "<<count<<'\n'; return 0;
}

int wmain(int argc,wchar_t** argv) {
    auto start=Clock::now();
    try { if(argc==2 && std::wstring(argv[1])==L"--self-test") return selfTest(); return scan(arguments(argc,argv),start); }
    catch(const std::exception& e) { std::cerr<<"Native scan failed: "<<e.what()<<" (Win32="<<GetLastError()<<")\n"; return 2; }
}
