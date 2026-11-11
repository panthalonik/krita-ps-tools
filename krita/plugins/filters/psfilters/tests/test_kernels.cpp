// Compares ps_kernels.h with the NumPy reference of the Python plugin
// (vectors written by gen_reference.py) and checks Blend If / half floats.
#include "../ps_kernels.h"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

static std::vector<uint8_t> slurp(const std::string &p)
{
    std::ifstream f(p, std::ios::binary);
    if (!f) { std::cerr << "cannot read " << p << "\n"; std::exit(2); }
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
}

static int failures = 0;
#define CHECK(cond, msg) do { if (!(cond)) { ++failures; std::cerr << "FAIL: " << msg << "\n"; } } while (0)

template<typename T, typename Op>
static void run(const std::string &depth, const std::vector<uint8_t> &src, std::vector<uint8_t> &dst,
                int n, const Op &op)
{
    if (depth == "F32") ps::runColor<T>(src.data(), dst.data(), n, 0, 1, 2, 3, op);
    else ps::runColor<T>(src.data(), dst.data(), n, 2, 1, 0, 3, op);
}

template<typename Op>
static void runDepth(const std::string &depth, const std::vector<uint8_t> &src,
                     std::vector<uint8_t> &dst, int n, const Op &op)
{
    if (depth == "U8") run<uint8_t>(depth, src, dst, n, op);
    else if (depth == "U16") run<uint16_t>(depth, src, dst, n, op);
    else run<float>(depth, src, dst, n, op);
}

// max |a - b| per channel; integer depths in code values, float in units
static double maxDiff(const std::string &depth, const std::vector<uint8_t> &a, const std::vector<uint8_t> &b,
                      long *off)
{
    double m = 0;
    *off = 0;
    size_t count = depth == "U8" ? a.size() : depth == "U16" ? a.size() / 2 : a.size() / 4;
    long bad = 0;
    for (size_t i = 0; i < count; i++) {
        double d;
        if (depth == "U8") d = std::fabs((double)a[i] - (double)b[i]);
        else if (depth == "U16") {
            uint16_t x, y; std::memcpy(&x, &a[i * 2], 2); std::memcpy(&y, &b[i * 2], 2);
            d = std::fabs((double)x - (double)y);
        } else {
            float x, y; std::memcpy(&x, &a[i * 4], 4); std::memcpy(&y, &b[i * 4], 4);
            d = std::fabs((double)x - (double)y);
        }
        if (d > 0) ++bad;
        if (d > m) m = d;
    }
    *off = bad;
    return m;
}

int main(int argc, char **argv)
{
    if (argc < 2) { std::cerr << "usage: test_kernels <vector dir>\n"; return 2; }
    const std::string dir = std::string(argv[1]) + "/";
    std::ifstream cases(dir + "cases.txt");
    int n;
    cases >> n;
    std::string kind, base, depth, name, dash;
    int flag;
    int count = 0;
    while (cases >> kind >> base >> depth >> flag >> name >> dash) {
        ++count;
        std::vector<uint8_t> src = slurp(dir + base + ".src"), ref = slurp(dir + base + ".ref");
        std::vector<uint8_t> dst(src.size());
        if (kind == "lut") {
            std::vector<uint8_t> blob = slurp(dir + base + ".lut");
            ps::Lut L;
            CHECK(ps::parseLut(blob.data(), blob.size(), L), base << " lut parse");
            L.linear = flag != 0;
            runDepth(depth, src, dst, n, [&L](float rgb[3]) { ps::applyLut(L, rgb); });
        } else {
            std::vector<uint8_t> adj = slurp(dir + base + ".adj");
            ps::SelectiveColor S;
            S.set(reinterpret_cast<const float *>(adj.data()), flag != 0);
            runDepth(depth, src, dst, n, [&S](float rgb[3]) { ps::selectiveColor(S, rgb); });
        }
        long off;
        double m = maxDiff(depth, dst, ref, &off);
        // integer depths: rounding of the last float bit may flip a code value
        double tol = depth == "U8" ? 1 : depth == "U16" ? 2 : 2e-5;
        double share = (double)off / (double)(n * 4);
        printf("%-4s %s %-3s flag=%d %-7s max diff %-10.3g differing %.3f%%\n", kind.c_str(), base.c_str(),
               depth.c_str(), flag, name.c_str(), m, share * 100);
        CHECK(m <= tol, base << " " << kind << " " << depth << " max diff " << m);
        if (depth != "F32") CHECK(share < 0.01, base << " too many differing values: " << share);
    }
    CHECK(count == 54, "expected 54 cases, got " << count);

    // in place == out of place; alpha 0 pixels untouched; alpha never changed
    {
        std::vector<uint8_t> src = slurp(dir + "c00.src"), a(src.size()), b = src;
        std::vector<uint8_t> blob = slurp(dir + "c00.lut");
        ps::Lut L; ps::parseLut(blob.data(), blob.size(), L);
        auto op = [&L](float rgb[3]) { ps::applyLut(L, rgb); };
        ps::runColor<uint8_t>(src.data(), a.data(), n, 2, 1, 0, 3, op);
        ps::runColor<uint8_t>(b.data(), b.data(), n, 2, 1, 0, 3, op);
        CHECK(a == b, "in-place result differs");
        bool ok = true;
        for (int i = 0; i < n; i++) {
            if (a[i * 4 + 3] != src[i * 4 + 3]) ok = false;
            if (src[i * 4 + 3] == 0 && std::memcmp(&a[i * 4], &src[i * 4], 4) != 0) ok = false;
        }
        CHECK(ok, "alpha changed or transparent pixel touched");
    }

    // malformed LUT blobs are rejected
    {
        std::vector<uint8_t> blob = slurp(dir + "c00.lut");
        ps::Lut L;
        CHECK(!ps::parseLut(blob.data(), blob.size() - 1, L), "truncated blob accepted");
        CHECK(!ps::parseLut(blob.data(), 10, L), "tiny blob accepted");
        std::vector<uint8_t> bad = blob; bad[0] = 'X';
        CHECK(!ps::parseLut(bad.data(), bad.size(), L), "bad magic accepted");
        bad = blob; int32_t big = 100000; std::memcpy(&bad[4], &big, 4);
        CHECK(!ps::parseLut(bad.data(), bad.size(), L), "huge size accepted");
        bad = blob; float z = 0; std::memcpy(&bad[24], &z, 4);          // dmax == dmin
        CHECK(!ps::parseLut(bad.data(), bad.size(), L), "empty domain accepted");
    }

    // Blend If: the same trapezoid as the Python make_lut(), on every 8-bit level
    {
        int sets[][4] = {{0, 0, 255, 255}, {10, 20, 200, 255}, {50, 50, 100, 100}, {0, 255, 255, 255},
                         {0, 0, 0, 255}, {128, 128, 128, 128}, {30, 90, 91, 92}};
        for (auto &s : sets) {
            ps::BlendIf B; B.channel = 1; B.b0 = s[0]; B.b1 = s[1]; B.w0 = s[2]; B.w1 = s[3];
            for (int v = 0; v < 256; v++) {
                double a;
                if (v < s[0]) a = 0;
                else if (v < s[1]) a = (v - s[0]) / (double)(s[1] - s[0]);
                else if (v <= s[2]) a = 1;
                else if (v <= s[3]) a = (s[3] - v) / (double)(s[3] - s[2]);
                else a = 0;
                int want = (int)std::floor(a * 255 + 0.5);
                uint8_t px[4] = {0, 0, (uint8_t)v, 255};       // BGRA, red = v
                ps::runAlpha<uint8_t>(px, px, 1, 2, 1, 0, 3,
                                      [&B](const float rgb[3]) { return ps::blendIfFactor(B, rgb); });
                CHECK(std::abs((int)px[3] - want) <= 1, "blend if " << s[0] << "/" << s[1] << " " << s[2] << "/"
                      << s[3] << " v=" << v << " got " << (int)px[3] << " want " << want);
                CHECK(px[2] == v && px[0] == 0, "blend if changed colour");
            }
        }
        ps::BlendIf B;
        CHECK(B.neutral(), "default not neutral");
        float gray[3] = {1.0f, 1.0f, 1.0f};
        B.w0 = 254; B.w1 = 254;
        CHECK(ps::blendIfFactor(B, gray) == 0.0f, "white not cut");
        uint16_t p16[4] = {0, 0, 0, 40000};
        B = ps::BlendIf(); B.b0 = 10; B.b1 = 10;
        ps::runAlpha<uint16_t>((uint8_t *)p16, (uint8_t *)p16, 1, 2, 1, 0, 3,
                               [&B](const float rgb[3]) { return ps::blendIfFactor(B, rgb); });
        CHECK(p16[3] == 0, "16-bit black not cut");
    }

    // half floats: every finite half survives the round trip, known values
    {
        bool ok = true;
        for (uint32_t h = 0; h < 65536; h++) {
            if (((h >> 10) & 0x1F) == 31) continue;
            if (ps::floatToHalf(ps::halfToFloat((uint16_t)h)) != h) ok = false;
        }
        CHECK(ok, "half round trip");
        CHECK(ps::halfToFloat(0x3C00) == 1.0f, "half 1.0");
        CHECK(ps::halfToFloat(0x3800) == 0.5f, "half 0.5");
        CHECK(ps::halfToFloat(0xC000) == -2.0f, "half -2.0");
        CHECK(ps::floatToHalf(65536.0f * 4) == 0x7C00, "half overflow");
        ps::Half px[4] = {{0x3800}, {0x3800}, {0x3800}, {0x3C00}};
        ps::SelectiveColor S; float adj[36] = {0}; adj[7 * 4 + 3] = 0.5f; S.set(adj, false);
        ps::runColor<ps::Half>((uint8_t *)px, (uint8_t *)px, 1, 0, 1, 2, 3,
                               [&S](float rgb[3]) { ps::selectiveColor(S, rgb); });
        float got = ps::halfToFloat(px[0].bits);
        CHECK(std::fabs(got - 0.0f) < 1e-3, "half selective color: neutrals +50% black on mid gray, got " << got);
    }

    if (failures) { std::cerr << failures << " failure(s)\n"; return 1; }
    std::cout << "all kernel checks passed (" << count << " reference cases)\n";
    return 0;
}
