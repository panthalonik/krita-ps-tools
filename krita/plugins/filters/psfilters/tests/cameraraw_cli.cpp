// Runs the Camera Raw kernel on a raw image: used by test_cameraraw.py
// (tile consistency, sanity checks, preview images).
//
//   cameraraw_cli in.raw out.raw [tile=N] [lod=N] key=value ...
//
// in.raw / out.raw: int32 width, int32 height, then width*height*4 float32
// RGBA (sRGB-encoded colour, 0..1). With tile=N the image is processed in
// N x N tiles, each with border() pixels of context, like Krita does.
#include "../ps_cameraraw.h"

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <string>

using namespace ps::cr;

static void run(const Settings &s, const ToneLuts &tone, const std::vector<float> &img, int W, int H,
                std::vector<float> &out, int x, int y, int w, int h, int lod)
{
    const int b = border(s, lod);
    const int bx0 = std::max(0, x - b), by0 = std::max(0, y - b);
    const int bx1 = std::min(W, x + w + b), by1 = std::min(H, y + h + b);
    Buffer buf;
    buf.width = bx1 - bx0;
    buf.height = by1 - by0;
    buf.x0 = bx0;
    buf.y0 = by0;
    const size_t n = size_t(buf.width) * buf.height;
    buf.r.resize(n); buf.g.resize(n); buf.b.resize(n); buf.a.resize(n);
    for (int yy = 0; yy < buf.height; yy++) {
        for (int xx = 0; xx < buf.width; xx++) {
            const float *p = &img[(size_t(by0 + yy) * W + (bx0 + xx)) * 4];
            const size_t i = size_t(yy) * buf.width + xx;
            buf.r[i] = srgbDecode(p[0]);
            buf.g[i] = srgbDecode(p[1]);
            buf.b[i] = srgbDecode(p[2]);
            buf.a[i] = p[3];
        }
    }
    Frame fr;
    fr.x = 0; fr.y = 0; fr.width = W; fr.height = H; fr.lod = lod;
    Target t;
    t.x = x; t.y = y; t.width = w; t.height = h;
    process(s, buf, fr, tone, t);
    for (int yy = y; yy < y + h; yy++) {
        for (int xx = x; xx < x + w; xx++) {
            const size_t i = size_t(yy - buf.y0) * buf.width + (xx - buf.x0);
            float *q = &out[(size_t(yy) * W + xx) * 4];
            q[0] = srgbEncode(std::max(0.0f, buf.r[i]));
            q[1] = srgbEncode(std::max(0.0f, buf.g[i]));
            q[2] = srgbEncode(std::max(0.0f, buf.b[i]));
            q[3] = buf.a[i];
        }
    }
}

int main(int argc, char **argv)
{
    if (argc < 3) {
        std::cerr << "usage: cameraraw_cli in.raw out.raw [tile=N] [lod=N] key=value...\n";
        return 2;
    }
    std::ifstream in(argv[1], std::ios::binary);
    int32_t W = 0, H = 0;
    in.read(reinterpret_cast<char *>(&W), 4);
    in.read(reinterpret_cast<char *>(&H), 4);
    std::vector<float> img(size_t(W) * H * 4);
    in.read(reinterpret_cast<char *>(img.data()), img.size() * 4);
    if (!in) { std::cerr << "bad input\n"; return 2; }

    Settings s;
    int tile = 0, lod = 0;
    for (int i = 3; i < argc; i++) {
        const std::string a = argv[i];
        const size_t eq = a.find('=');
        if (eq == std::string::npos) continue;
        const std::string k = a.substr(0, eq), v = a.substr(eq + 1);
        if (k == "tile") tile = std::atoi(v.c_str());
        else if (k == "lod") lod = std::atoi(v.c_str());
        else if (k.rfind("curve_", 0) == 0) setCurve(s, k, v);
        else if (!setValue(s, k, float(std::atof(v.c_str())))) {
            std::cerr << "unknown setting " << k << "\n";
            return 2;
        }
    }
    ToneLuts tone;
    tone.build(s);
    std::vector<float> out(img);
    if (tile <= 0) {
        run(s, tone, img, W, H, out, 0, 0, W, H, lod);
    } else {
        for (int y = 0; y < H; y += tile)
            for (int x = 0; x < W; x += tile)
                run(s, tone, img, W, H, out, x, y, std::min(tile, W - x), std::min(tile, H - y), lod);
    }
    std::ofstream o(argv[2], std::ios::binary);
    o.write(reinterpret_cast<const char *>(&W), 4);
    o.write(reinterpret_cast<const char *>(&H), 4);
    o.write(reinterpret_cast<const char *>(out.data()), out.size() * 4);
    std::cout << "border=" << border(s, lod) << "\n";
    return 0;
}
