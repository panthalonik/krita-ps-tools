/*
 *  Pixel kernels of the Photoshop-style filters (Color Lookup, Selective
 *  Color, Blend If). Plain C++, no Krita / Qt dependencies, so that they can
 *  be tested on their own. The maths mirrors the reference implementation of
 *  the ps_tools Python plugin (pixelops.py), value for value.
 *
 *  SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef PS_KERNELS_H
#define PS_KERNELS_H

#include <cmath>
#include <cstdint>
#include <cstring>
#include <vector>

namespace ps {

// ------------------------------------------------------------- helpers
inline float clamp01(float v) { return v < 0.0f ? 0.0f : (v > 1.0f ? 1.0f : v); }

inline float srgbToLinear(float v)
{
    return v <= 0.04045f ? v / 12.92f : std::pow((v + 0.055f) / 1.055f, 2.4f);
}

inline float linearToSrgb(float v)
{
    if (v < 0.0f) v = 0.0f;
    return v <= 0.0031308f ? v * 12.92f : 1.055f * std::pow(v, 1.0f / 2.4f) - 0.055f;
}

// IEEE 754 half <-> float (Krita's 16-bit float channels), no OpenEXR needed
inline float halfToFloat(uint16_t h)
{
    uint32_t sign = (uint32_t)(h & 0x8000u) << 16;
    uint32_t exp = (h >> 10) & 0x1Fu;
    uint32_t man = h & 0x3FFu;
    uint32_t bits;
    if (exp == 0) {
        if (man == 0) {
            bits = sign;
        } else {                              // subnormal
            int e = -1;
            do { ++e; man <<= 1; } while (!(man & 0x400u));
            bits = sign | (uint32_t)(127 - 15 - e) << 23 | (man & 0x3FFu) << 13;
        }
    } else if (exp == 31) {
        bits = sign | 0x7F800000u | man << 13;
    } else {
        bits = sign | (exp + 127 - 15) << 23 | man << 13;
    }
    float f;
    std::memcpy(&f, &bits, 4);
    return f;
}

inline uint16_t floatToHalf(float f)
{
    uint32_t x;
    std::memcpy(&x, &f, 4);
    uint16_t sign = (uint16_t)((x >> 16) & 0x8000u);
    int32_t exp = (int32_t)((x >> 23) & 0xFFu) - 127 + 15;
    uint32_t man = x & 0x7FFFFFu;
    if (((x >> 23) & 0xFFu) == 0xFFu) {       // inf / nan
        return (uint16_t)(sign | 0x7C00u | (man ? 0x200u : 0u));
    }
    if (exp >= 31) return (uint16_t)(sign | 0x7C00u);      // overflow -> inf
    if (exp <= 0) {
        if (exp < -10) return sign;                        // underflow -> 0
        man |= 0x800000u;
        uint32_t shift = (uint32_t)(14 - exp);
        uint32_t half = man >> shift;
        uint32_t rem = man & ((1u << shift) - 1u);
        uint32_t mid = 1u << (shift - 1);
        if (rem > mid || (rem == mid && (half & 1u))) ++half;
        return (uint16_t)(sign | half);
    }
    uint32_t half = ((uint32_t)exp << 10) | (man >> 13);
    uint32_t rem = man & 0x1FFFu;
    if (rem > 0x1000u || (rem == 0x1000u && (half & 1u))) ++half;   // may carry into exp: fine
    return (uint16_t)(sign | half);
}

// ------------------------------------------------------------------ LUT
struct Lut {
    int n3 = 0;                 // 3D size (0 = no 3D table)
    int n1 = 0;                 // 1D size (0 = none; with n3: pre-shaper)
    float dmin[3] = {0, 0, 0};
    float dmax[3] = {1, 1, 1};
    std::vector<float> d1;      // n1 * 3            [i][channel]
    std::vector<float> d3;      // n3^3 * 3          [b][g][r][channel]
    bool linear = false;        // table expects linear light (sRGB transfer undone)

    bool valid() const
    {
        if (n3 == 0 && n1 == 0) return false;
        if (n3 == 1 || n1 == 1 || n3 < 0 || n1 < 0) return false;
        if (d1.size() != (size_t)n1 * 3) return false;
        if (d3.size() != (size_t)n3 * n3 * n3 * 3) return false;
        for (int k = 0; k < 3; k++) if (!(dmax[k] > dmin[k])) return false;
        return true;
    }
};

inline void lut1d(const Lut &L, float c[3])
{
    const int n = L.n1;
    const float *T = L.d1.data();
    for (int k = 0; k < 3; k++) {
        float x = clamp01(c[k]) * (float)(n - 1);
        int i = (int)x;
        if (i > n - 2) i = n - 2;
        float f = x - (float)i;
        c[k] = T[i * 3 + k] * (1.0f - f) + T[(i + 1) * 3 + k] * f;
    }
}

/// rgb: normalised (0..1 for integer depths), modified in place. Not clamped.
/// linear: the table works on linear light (the sRGB curve is undone first).
inline void applyLut(const Lut &L, float rgb[3], bool linear)
{
    float t[3];
    for (int k = 0; k < 3; k++) {
        float v = rgb[k];
        if (linear) v = srgbToLinear(v);
        t[k] = clamp01((v - L.dmin[k]) / (L.dmax[k] - L.dmin[k]));
    }
    float out[3];
    if (L.n3) {
        if (L.n1) lut1d(L, t);
        const int n = L.n3;
        int i0[3];
        float f[3];
        for (int k = 0; k < 3; k++) {
            float x = t[k] * (float)(n - 1);
            int i = (int)x;
            if (i > n - 2) i = n - 2;
            if (i < 0) i = 0;
            i0[k] = i;
            f[k] = x - (float)i;
        }
        out[0] = out[1] = out[2] = 0.0f;
        const float *D = L.d3.data();
        for (int db = 0; db < 2; db++) {
            const float wb = db ? f[2] : 1.0f - f[2];
            for (int dg = 0; dg < 2; dg++) {
                const float wg = dg ? f[1] : 1.0f - f[1];
                for (int dr = 0; dr < 2; dr++) {
                    const float w = wb * wg * (dr ? f[0] : 1.0f - f[0]);
                    const float *p = D + ((size_t)((i0[2] + db) * n + i0[1] + dg) * n + i0[0] + dr) * 3;
                    out[0] += w * p[0];
                    out[1] += w * p[1];
                    out[2] += w * p[2];
                }
            }
        }
    } else {
        lut1d(L, t);
        out[0] = t[0]; out[1] = t[1]; out[2] = t[2];
    }
    for (int k = 0; k < 3; k++) rgb[k] = linear ? linearToSrgb(out[k]) : out[k];
}

inline void applyLut(const Lut &L, float rgb[3]) { applyLut(L, rgb, L.linear); }

// ------------------------------------------------------ Selective Color
/// adj: 9 ranges (reds, yellows, greens, cyans, blues, magentas, whites,
/// neutrals, blacks) x (cyan, magenta, yellow, black), each -1..1
struct SelectiveColor {
    float adj[36];
    bool relative = true;
    bool used[9];
    bool any = false;

    void set(const float values[36], bool rel)
    {
        relative = rel;
        any = false;
        for (int k = 0; k < 9; k++) {
            used[k] = false;
            for (int c = 0; c < 4; c++) {
                float v = values[k * 4 + c];
                if (v < -1.0f) v = -1.0f;
                if (v > 1.0f) v = 1.0f;
                adj[k * 4 + c] = v;
                if (v != 0.0f) used[k] = true;
            }
            any = any || used[k];
        }
    }
};

/// rgb: normalised, modified in place. Not clamped (the input is).
inline void selectiveColor(const SelectiveColor &S, float rgb[3])
{
    const float r = clamp01(rgb[0]), g = clamp01(rgb[1]), b = clamp01(rgb[2]);
    const float mx = r > g ? (r > b ? r : b) : (g > b ? g : b);
    const float mn = r < g ? (r < b ? r : b) : (g < b ? g : b);
    const float mid = r + g + b - mx - mn;
    float rng[9];
    rng[0] = r == mx ? mx - mid : 0.0f;
    rng[1] = b == mn ? mid - mn : 0.0f;
    rng[2] = g == mx ? mx - mid : 0.0f;
    rng[3] = r == mn ? mid - mn : 0.0f;
    rng[4] = b == mx ? mx - mid : 0.0f;
    rng[5] = g == mn ? mid - mn : 0.0f;
    rng[6] = (r > 0.5f && g > 0.5f && b > 0.5f) ? (mn - 0.5f) * 2.0f : 0.0f;
    rng[7] = ((r > 0.0f || g > 0.0f || b > 0.0f) && !(r >= 1.0f && g >= 1.0f && b >= 1.0f))
        ? 1.0f - (std::fabs(mx - 0.5f) + std::fabs(mn - 0.5f)) : 0.0f;
    rng[8] = (r < 0.5f && g < 0.5f && b < 0.5f) ? (0.5f - mx) * 2.0f : 0.0f;
    const float ch[3] = {r, g, b};
    float d[3] = {0.0f, 0.0f, 0.0f};
    for (int k = 0; k < 9; k++) {
        if (!S.used[k]) continue;
        const float w = rng[k] > 0.0f ? rng[k] : 0.0f;
        if (w == 0.0f) continue;
        const float *a = S.adj + k * 4;
        for (int c = 0; c < 3; c++) {
            const float v = ch[c];
            float res = (-1.0f - a[c]) * a[3] - a[c];
            if (S.relative) res *= 1.0f - v;
            if (res < -v) res = -v;
            if (res > 1.0f - v) res = 1.0f - v;
            d[c] += res * w;
        }
    }
    for (int c = 0; c < 3; c++) rgb[c] = ch[c] + d[c];
}

// -------------------------------------------------------------- Blend If
/// Photoshop "Blend If: This Layer". Levels are on the 0..255 scale (split
/// sliders: black0/black1, white0/white1). channel: 0 gray, 1 R, 2 G, 3 B.
struct BlendIf {
    int channel = 0;
    float b0 = 0, b1 = 0, w0 = 255, w1 = 255;

    bool neutral() const { return b0 <= 0 && b1 <= 0 && w0 >= 255 && w1 >= 255; }
};

/// -> opacity factor 0..1 for a pixel (rgb normalised)
inline float blendIfFactor(const BlendIf &B, const float rgb[3])
{
    float v;
    switch (B.channel) {
    case 1: v = rgb[0]; break;
    case 2: v = rgb[1]; break;
    case 3: v = rgb[2]; break;
    default: v = 0.30f * rgb[0] + 0.59f * rgb[1] + 0.11f * rgb[2];
    }
    v = clamp01(v) * 255.0f;
    if (v < B.b0) return 0.0f;
    if (v < B.b1) return (v - B.b0) / (B.b1 - B.b0);
    if (v <= B.w0) return 1.0f;
    if (v <= B.w1) return (B.w1 - v) / (B.w1 - B.w0);
    return 0.0f;
}

// ------------------------------------------------------ pixel buffer loop
template<typename T> struct Chan;
template<> struct Chan<uint8_t> {
    static float get(uint8_t v) { return (float)v / 255.0f; }
    static uint8_t put(float v) { return (uint8_t)(clamp01(v) * 255.0f + 0.5f); }
};
template<> struct Chan<uint16_t> {
    static float get(uint16_t v) { return (float)v / 65535.0f; }
    static uint16_t put(float v) { return (uint16_t)(clamp01(v) * 65535.0f + 0.5f); }
};
template<> struct Chan<float> {
    static float get(float v) { return v; }
    static float put(float v) { return v; }
};
struct Half { uint16_t bits; };             // tag type for 16-bit float channels
template<> struct Chan<Half> {
    static float get(Half v) { return halfToFloat(v.bits); }
    static Half put(float v) { Half h; h.bits = floatToHalf(v); return h; }
};

/// Colour kernels: op(float rgb[3]) on every pixel whose alpha is > 0.
/// src and dst may be the same buffer. 4 channels per pixel.
template<typename T, typename Op>
inline void runColor(const uint8_t *src, uint8_t *dst, int nPixels,
                     int rp, int gp, int bp, int ap, const Op &op)
{
    if (src != dst) std::memcpy(dst, src, (size_t)nPixels * 4 * sizeof(T));
    T *p = reinterpret_cast<T *>(dst);
    for (int i = 0; i < nPixels; i++, p += 4) {
        if (!(Chan<T>::get(p[ap]) > 0.0f)) continue;
        float rgb[3] = {Chan<T>::get(p[rp]), Chan<T>::get(p[gp]), Chan<T>::get(p[bp])};
        op(rgb);
        p[rp] = Chan<T>::put(rgb[0]);
        p[gp] = Chan<T>::put(rgb[1]);
        p[bp] = Chan<T>::put(rgb[2]);
    }
}

/// Alpha kernels: alpha *= op(const float rgb[3]). Colour is not touched.
template<typename T, typename Op>
inline void runAlpha(const uint8_t *src, uint8_t *dst, int nPixels,
                     int rp, int gp, int bp, int ap, const Op &op)
{
    if (src != dst) std::memcpy(dst, src, (size_t)nPixels * 4 * sizeof(T));
    T *p = reinterpret_cast<T *>(dst);
    for (int i = 0; i < nPixels; i++, p += 4) {
        const float a = Chan<T>::get(p[ap]);
        if (!(a > 0.0f)) continue;
        const float rgb[3] = {Chan<T>::get(p[rp]), Chan<T>::get(p[gp]), Chan<T>::get(p[bp])};
        const float f = op(rgb);
        if (f < 1.0f) p[ap] = Chan<T>::put(a * f);
    }
}

// ------------------------------------------------- LUT wire format ("PSL1")
// little endian: "PSL1", int32 n3, int32 n1, float dmin[3], float dmax[3],
// float d1[n1*3], float d3[n3^3*3]
inline bool parseLut(const uint8_t *data, size_t size, Lut &L)
{
    const size_t header = 4 + 4 + 4 + 6 * 4;
    if (size < header || std::memcmp(data, "PSL1", 4) != 0) return false;
    int32_t n3, n1;
    std::memcpy(&n3, data + 4, 4);
    std::memcpy(&n1, data + 8, 4);
    if (n3 < 0 || n3 > 256 || n1 < 0 || n1 > 65536) return false;
    std::memcpy(L.dmin, data + 12, 12);
    std::memcpy(L.dmax, data + 24, 12);
    const size_t c1 = (size_t)n1 * 3, c3 = (size_t)n3 * n3 * n3 * 3;
    if (size != header + (c1 + c3) * 4) return false;
    L.n3 = n3;
    L.n1 = n1;
    L.d1.resize(c1);
    L.d3.resize(c3);
    if (c1) std::memcpy(L.d1.data(), data + header, c1 * 4);
    if (c3) std::memcpy(L.d3.data(), data + header + c1 * 4, c3 * 4);
    return L.valid();
}

} // namespace ps

#endif
