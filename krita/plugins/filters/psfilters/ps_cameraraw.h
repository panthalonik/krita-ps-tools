/*
 *  Camera Raw Filter (Photoshop-like): the Light, Color, Effects and Detail
 *  panels of Adobe Camera Raw, for already rendered pixels (like Photoshop's
 *  Filter > Camera Raw Filter on a non-raw layer).
 *
 *  Adobe's algorithms are not public; these are close equivalents built from
 *  well known methods (edge-aware guided filter for Highlights / Shadows /
 *  Clarity, dark channel prior for Dehaze, unsharp mask with halo control,
 *  guided-filter noise reduction, OKLab for colour work). The sliders have
 *  the same names, ranges and directions as in Camera Raw.
 *
 *  Plain C++, no Krita / Qt dependencies: tested on its own
 *  (tests/test_cameraraw.cpp). process() works on a float buffer that has a
 *  border around the area that is kept: with border() pixels of context the
 *  inner area is exactly the same as when the whole image is processed at
 *  once, so Krita can render it tile by tile.
 *
 *  SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef PS_CAMERARAW_H
#define PS_CAMERARAW_H

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

namespace ps {
namespace cr {

// --------------------------------------------------------------- settings
/// Slider values exactly as in Camera Raw (non-raw image ranges).
struct Settings {
    // ---- Light
    float exposure = 0;                 // -5 .. +5 EV
    float contrast = 0;                 // -100 .. 100
    float highlights = 0, shadows = 0;  // -100 .. 100
    float whites = 0, blacks = 0;       // -100 .. 100
    // parametric curve
    float pcHighlights = 0, pcLights = 0, pcDarks = 0, pcShadows = 0;  // -100 .. 100
    float pcSplit1 = 25, pcSplit2 = 50, pcSplit3 = 75;                // 0 .. 100
    // point curves, points (x, y) in 0..1; empty = straight line
    std::vector<std::pair<float, float>> curveRgb, curveRed, curveGreen, curveBlue;

    // ---- Color
    float temperature = 0, tint = 0;     // -100 .. 100 (non-raw images)
    float vibrance = 0, saturation = 0;  // -100 .. 100
    float bw = 0;                        // 1: Black & White treatment (Color Mixer = B&W Mix)
    // Color Mixer (HSL), colours: red orange yellow green aqua blue purple magenta
    float mixHue[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    float mixSat[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    float mixLum[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    // Color Grading: shadows, midtones, highlights, global
    float cgHue[4] = {0, 0, 0, 0};       // 0 .. 360
    float cgSat[4] = {0, 0, 0, 0};       // 0 .. 100
    float cgLum[4] = {0, 0, 0, 0};       // -100 .. 100
    float cgBlending = 50, cgBalance = 0;

    // ---- Effects
    float texture = 0, clarity = 0, dehaze = 0;   // -100 .. 100
    int vigStyle = 0;                    // 0 highlight priority, 1 color priority, 2 paint overlay
    float vigAmount = 0;                 // -100 .. 100
    float vigMidpoint = 50, vigRoundness = 0, vigFeather = 50, vigHighlights = 0;
    float grainAmount = 0, grainSize = 25, grainRoughness = 50;   // 0 .. 100

    // ---- Detail
    float sharpAmount = 0;               // 0 .. 150
    float sharpRadius = 1.0f;            // 0.5 .. 3.0
    float sharpDetail = 25, sharpMasking = 0;
    float nrLum = 0, nrLumDetail = 50, nrLumContrast = 0;
    float nrColor = 0, nrColorDetail = 50, nrColorSmooth = 50;

    bool hasCurve(const std::vector<std::pair<float, float>> &c) const {
        if (c.size() < 2) return false;
        for (const auto &p : c) {
            if (std::fabs(p.first - p.second) > 1e-6f) return true;
        }
        return false;
    }
    bool toneActive() const {
        return contrast != 0 || whites != 0 || blacks != 0 || pcHighlights != 0 || pcLights != 0 ||
               pcDarks != 0 || pcShadows != 0 || hasCurve(curveRgb) || hasCurve(curveRed) ||
               hasCurve(curveGreen) || hasCurve(curveBlue);
    }
    bool colorActive() const {
        if (vibrance != 0 || saturation != 0 || bw != 0) return true;
        for (int i = 0; i < 8; i++) {
            if (mixHue[i] != 0 || mixSat[i] != 0 || mixLum[i] != 0) return true;
        }
        for (int i = 0; i < 4; i++) {
            if (cgSat[i] != 0 || cgLum[i] != 0) return true;
        }
        return false;
    }
    bool isIdentity() const {
        return exposure == 0 && highlights == 0 && shadows == 0 && temperature == 0 && tint == 0 &&
               !toneActive() && !colorActive() && texture == 0 && clarity == 0 && dehaze == 0 &&
               vigAmount == 0 && grainAmount == 0 && sharpAmount == 0 && nrLum == 0 && nrColor == 0;
    }
};

// ---------------------------------------------------- settings by name
/// Names of the numeric settings (the filter configuration's properties and
/// the ps_tools plugin's parameters use the same names).
inline const char *const *settingNames(int &count)
{
    static const char *const names[] = {
        "exposure", "contrast", "highlights", "shadows", "whites", "blacks",
        "pc_highlights", "pc_lights", "pc_darks", "pc_shadows", "pc_split1", "pc_split2", "pc_split3",
        "temperature", "tint", "vibrance", "saturation", "bw",
        "mix_hue_red", "mix_hue_orange", "mix_hue_yellow", "mix_hue_green", "mix_hue_aqua", "mix_hue_blue",
        "mix_hue_purple", "mix_hue_magenta",
        "mix_sat_red", "mix_sat_orange", "mix_sat_yellow", "mix_sat_green", "mix_sat_aqua", "mix_sat_blue",
        "mix_sat_purple", "mix_sat_magenta",
        "mix_lum_red", "mix_lum_orange", "mix_lum_yellow", "mix_lum_green", "mix_lum_aqua", "mix_lum_blue",
        "mix_lum_purple", "mix_lum_magenta",
        "cg_shadows_hue", "cg_shadows_sat", "cg_shadows_lum", "cg_midtones_hue", "cg_midtones_sat",
        "cg_midtones_lum", "cg_highlights_hue", "cg_highlights_sat", "cg_highlights_lum", "cg_global_hue",
        "cg_global_sat", "cg_global_lum", "cg_blending", "cg_balance",
        "texture", "clarity", "dehaze",
        "vig_style", "vig_amount", "vig_midpoint", "vig_roundness", "vig_feather", "vig_highlights",
        "grain_amount", "grain_size", "grain_roughness",
        "sharp_amount", "sharp_radius", "sharp_detail", "sharp_masking",
        "nr_lum", "nr_lum_detail", "nr_lum_contrast", "nr_color", "nr_color_detail", "nr_color_smooth",
    };
    count = int(sizeof(names) / sizeof(names[0]));
    return names;
}

/// Names of the point curves: "x,y;x,y;..." with values 0..1.
inline const char *const *curveNames(int &count)
{
    static const char *const names[] = {"curve_rgb", "curve_red", "curve_green", "curve_blue"};
    count = 4;
    return names;
}

inline bool setValue(Settings &s, const std::string &k, float v)
{
    static const char *colors[8] = {"red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"};
    static const char *zones[4] = {"shadows", "midtones", "highlights", "global"};
    struct F { const char *name; float Settings::*field; };
    static const F fields[] = {
        {"exposure", &Settings::exposure}, {"contrast", &Settings::contrast},
        {"highlights", &Settings::highlights}, {"shadows", &Settings::shadows},
        {"whites", &Settings::whites}, {"blacks", &Settings::blacks},
        {"pc_highlights", &Settings::pcHighlights}, {"pc_lights", &Settings::pcLights},
        {"pc_darks", &Settings::pcDarks}, {"pc_shadows", &Settings::pcShadows},
        {"pc_split1", &Settings::pcSplit1}, {"pc_split2", &Settings::pcSplit2}, {"pc_split3", &Settings::pcSplit3},
        {"temperature", &Settings::temperature}, {"tint", &Settings::tint},
        {"vibrance", &Settings::vibrance}, {"saturation", &Settings::saturation}, {"bw", &Settings::bw},
        {"cg_blending", &Settings::cgBlending}, {"cg_balance", &Settings::cgBalance},
        {"texture", &Settings::texture}, {"clarity", &Settings::clarity}, {"dehaze", &Settings::dehaze},
        {"vig_amount", &Settings::vigAmount}, {"vig_midpoint", &Settings::vigMidpoint},
        {"vig_roundness", &Settings::vigRoundness}, {"vig_feather", &Settings::vigFeather},
        {"vig_highlights", &Settings::vigHighlights},
        {"grain_amount", &Settings::grainAmount}, {"grain_size", &Settings::grainSize},
        {"grain_roughness", &Settings::grainRoughness},
        {"sharp_amount", &Settings::sharpAmount}, {"sharp_radius", &Settings::sharpRadius},
        {"sharp_detail", &Settings::sharpDetail}, {"sharp_masking", &Settings::sharpMasking},
        {"nr_lum", &Settings::nrLum}, {"nr_lum_detail", &Settings::nrLumDetail},
        {"nr_lum_contrast", &Settings::nrLumContrast}, {"nr_color", &Settings::nrColor},
        {"nr_color_detail", &Settings::nrColorDetail}, {"nr_color_smooth", &Settings::nrColorSmooth},
    };
    if (!std::isfinite(v)) return false;
    for (const F &f : fields) {
        if (k == f.name) {
            s.*(f.field) = v;
            return true;
        }
    }
    if (k == "vig_style") {
        s.vigStyle = std::max(0, std::min(2, int(std::lround(v))));
        return true;
    }
    for (int i = 0; i < 8; i++) {
        if (k == std::string("mix_hue_") + colors[i]) { s.mixHue[i] = v; return true; }
        if (k == std::string("mix_sat_") + colors[i]) { s.mixSat[i] = v; return true; }
        if (k == std::string("mix_lum_") + colors[i]) { s.mixLum[i] = v; return true; }
    }
    for (int z = 0; z < 4; z++) {
        const std::string p = std::string("cg_") + zones[z];
        if (k == p + "_hue") { s.cgHue[z] = v; return true; }
        if (k == p + "_sat") { s.cgSat[z] = v; return true; }
        if (k == p + "_lum") { s.cgLum[z] = v; return true; }
    }
    return false;
}

/// "x,y;x,y;..." (0..1) -> points; anything unreadable is skipped.
inline std::vector<std::pair<float, float>> parseCurve(const std::string &text)
{
    std::vector<std::pair<float, float>> out;
    size_t pos = 0;
    while (pos < text.size()) {
        size_t end = text.find(';', pos);
        if (end == std::string::npos) end = text.size();
        const std::string item = text.substr(pos, end - pos);
        const size_t comma = item.find(',');
        if (comma != std::string::npos) {
            char *e1 = nullptr, *e2 = nullptr;
            const std::string xs = item.substr(0, comma), ys = item.substr(comma + 1);
            const double x = std::strtod(xs.c_str(), &e1), y = std::strtod(ys.c_str(), &e2);
            if (e1 != xs.c_str() && e2 != ys.c_str() && std::isfinite(x) && std::isfinite(y)) {
                out.push_back(std::make_pair(float(std::min(1.0, std::max(0.0, x))),
                                             float(std::min(1.0, std::max(0.0, y)))));
            }
        }
        pos = end + 1;
    }
    return out;
}

inline bool setCurve(Settings &s, const std::string &k, const std::string &text)
{
    std::vector<std::pair<float, float>> *c = k == "curve_rgb" ? &s.curveRgb : k == "curve_red" ? &s.curveRed
                                            : k == "curve_green" ? &s.curveGreen
                                            : k == "curve_blue" ? &s.curveBlue : nullptr;
    if (!c) return false;
    *c = parseCurve(text);
    return true;
}

// ------------------------------------------------------------ radii / border
/// Radii (full-resolution pixels) of the local operations; at a level of
/// detail (Krita's Instant Preview) everything is 2^lod times smaller.
struct Radii {
    int nrLum = 0, nrColor = 0, dehazeMin = 0, dehazeGuide = 0, toneGuide = 0, clarityGuide = 0;
    int texture = 0, sharpen = 0, sharpenBlur[3] = {0, 0, 0}, textureBlur[3] = {0, 0, 0};
    int border = 0;
};

inline int scaled(float r, int lod)
{
    const float s = r / float(1 << std::max(0, std::min(lod, 8)));
    return std::max(1, int(std::lround(s)));
}

/// Box radii of three box blurs approximating a gaussian of sigma s.
inline void gaussBoxes(float sigma, int out[3])
{
    // n = 3 boxes of width w: sigma^2 = 3 (w^2 - 1) / 12  ->  w = sqrt(4 sigma^2 + 1)
    const float w = std::sqrt(4.0f * sigma * sigma + 1.0f);
    int r = std::max(1, int(std::lround((w - 1.0f) / 2.0f)));
    out[0] = out[1] = out[2] = r;
}

inline Radii radii(const Settings &s, int lod)
{
    Radii r;
    const float k = 1.0f / float(1 << std::max(0, std::min(lod, 8)));
    if (s.nrLum > 0) {
        r.nrLum = scaled(2.0f + s.nrLum / 100.0f * 3.0f, lod);
        r.border += 2 * r.nrLum;
    }
    if (s.nrColor > 0) {
        r.nrColor = scaled(2.0f + s.nrColor / 100.0f * 4.0f + s.nrColorSmooth / 100.0f * 6.0f, lod);
        r.border += 2 * r.nrColor;
    }
    if (s.dehaze != 0) {
        r.dehazeMin = scaled(6.0f, lod);
        r.dehazeGuide = scaled(20.0f, lod);
        r.border += r.dehazeMin + 2 * r.dehazeGuide;
    }
    if (s.highlights != 0 || s.shadows != 0) {
        r.toneGuide = scaled(24.0f, lod);
        r.border += 2 * r.toneGuide;
    }
    if (s.clarity != 0) {
        r.clarityGuide = scaled(32.0f, lod);
        r.border += 2 * r.clarityGuide;
    }
    if (s.texture != 0) {
        gaussBoxes(std::max(0.6f, 2.0f * k), r.textureBlur);
        r.texture = r.textureBlur[0] + r.textureBlur[1] + r.textureBlur[2];
        r.border += r.texture;
    }
    if (s.sharpAmount > 0) {
        gaussBoxes(std::max(0.5f, std::min(3.0f, s.sharpRadius) * k), r.sharpenBlur);
        r.sharpen = r.sharpenBlur[0] + r.sharpenBlur[1] + r.sharpenBlur[2] + 1;   // +1: gradient
        r.border += r.sharpen;
    }
    return r;
}

inline int border(const Settings &s, int lod) { return radii(s, lod).border; }

// --------------------------------------------------------------- colour
static const float kPi = 3.14159265358979f;

inline float clampf(float v, float lo, float hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float smoothstep(float e0, float e1, float x)
{
    if (e1 <= e0) return x < e0 ? 0.0f : 1.0f;
    const float t = clampf((x - e0) / (e1 - e0), 0.0f, 1.0f);
    return t * t * (3.0f - 2.0f * t);
}

inline float srgbDecodeExact(float v)
{
    if (v <= 0.0f) return v / 12.92f;
    return v <= 0.04045f ? v / 12.92f : std::pow((v + 0.055f) / 1.055f, 2.4f);
}

inline float srgbEncodeExact(float v)
{
    if (v <= 0.0031308f) return v * 12.92f;
    return 1.055f * std::pow(v, 1.0f / 2.4f) - 0.055f;
}

/// Tables for the sRGB curves (the filter converts every pixel several
/// times; pow() would dominate): decode indexed by the value, encode by the
/// square root of the value (the curve is steep near 0). Error < 1e-5.
struct SrgbTables {
    static const int N = 4096;
    float dec[N + 1], enc[N + 1];
    SrgbTables() {
        for (int i = 0; i <= N; i++) {
            const float t = float(i) / float(N);
            dec[i] = srgbDecodeExact(t);
            enc[i] = srgbEncodeExact(t * t);
        }
    }
    static const SrgbTables &get() {
        static const SrgbTables t;
        return t;
    }
};

inline float srgbDecode(float v)
{
    if (!(v >= 0.0f && v <= 1.0f)) return srgbDecodeExact(v);
    const SrgbTables &t = SrgbTables::get();
    const float f = v * SrgbTables::N;
    const int i = std::min(SrgbTables::N - 1, int(f));
    return t.dec[i] + (t.dec[i + 1] - t.dec[i]) * (f - float(i));
}

inline float srgbEncode(float v)
{
    if (!(v >= 0.0f && v <= 1.0f)) return srgbEncodeExact(v);
    const SrgbTables &t = SrgbTables::get();
    const float f = std::sqrt(v) * SrgbTables::N;
    const int i = std::min(SrgbTables::N - 1, int(f));
    return t.enc[i] + (t.enc[i + 1] - t.enc[i]) * (f - float(i));
}

inline float luminance(float r, float g, float b) { return 0.2126f * r + 0.7152f * g + 0.0722f * b; }

/// Cube root: a bit-level first guess and two Newton steps (relative error
/// < 1e-6), several times faster than std::cbrt.
inline float cbrtf_(float x)
{
    if (x == 0.0f || !std::isfinite(x)) return x;
    const float ax = std::fabs(x);
    uint32_t bits;
    std::memcpy(&bits, &ax, 4);
    bits = bits / 3u + 709921077u;
    float y;
    std::memcpy(&y, &bits, 4);
    y = y - (y * y * y - ax) / (3.0f * y * y);
    y = y - (y * y * y - ax) / (3.0f * y * y);
    y = y - (y * y * y - ax) / (3.0f * y * y);
    return x < 0 ? -y : y;
}

/// linear sRGB -> OKLab (Björn Ottosson)
inline void toOklab(float r, float g, float b, float &L, float &A, float &B)
{
    const float l = cbrtf_(0.4122214708f * r + 0.5363325363f * g + 0.0514459929f * b);
    const float m = cbrtf_(0.2119034982f * r + 0.6806995451f * g + 0.1073969566f * b);
    const float s = cbrtf_(0.0883024619f * r + 0.2817188376f * g + 0.6299787005f * b);
    L = 0.2104542553f * l + 0.7936177850f * m - 0.0040720468f * s;
    A = 1.9779984951f * l - 2.4285922050f * m + 0.4505937099f * s;
    B = 0.0259040371f * l + 0.7827717662f * m - 0.8086757660f * s;
}

inline void fromOklab(float L, float A, float B, float &r, float &g, float &b)
{
    const float l_ = L + 0.3963377774f * A + 0.2158037573f * B;
    const float m_ = L - 0.1055613458f * A - 0.0638541728f * B;
    const float s_ = L - 0.0894841775f * A - 1.2914855480f * B;
    const float l = l_ * l_ * l_, m = m_ * m_ * m_, s = s_ * s_ * s_;
    r = 4.0767416621f * l - 3.3077115913f * m + 0.2309699292f * s;
    g = -1.2684380046f * l + 2.6097574011f * m - 0.3413193965f * s;
    b = -0.0041960863f * l - 0.7034186147f * m + 1.7076147010f * s;
}

/// The OKLab direction (a, b, unit length) of an HSL hue in degrees, used to
/// turn Color Grading hue angles (Camera Raw's wheel) into tints.
inline void hueDirection(float hueDeg, float &da, float &db)
{
    const float h = std::fmod(std::fmod(hueDeg, 360.0f) + 360.0f, 360.0f) / 60.0f;
    const float x = 1.0f - std::fabs(std::fmod(h, 2.0f) - 1.0f);
    float r = 0, g = 0, b = 0;
    if (h < 1) { r = 1; g = x; } else if (h < 2) { r = x; g = 1; } else if (h < 3) { g = 1; b = x; }
    else if (h < 4) { g = x; b = 1; } else if (h < 5) { r = x; b = 1; } else { r = 1; b = x; }
    float L, A, B;
    toOklab(srgbDecode(r), srgbDecode(g), srgbDecode(b), L, A, B);
    const float n = std::sqrt(A * A + B * B);
    da = n > 0 ? A / n : 0;
    db = n > 0 ? B / n : 0;
}

/// OKLab hue angles (degrees) of the Color Mixer colours.
inline const float *mixerHues()
{
    // red, orange, yellow, green, aqua, blue, purple, magenta
    static const float h[8] = {29.2f, 55.0f, 109.8f, 142.5f, 194.8f, 264.1f, 300.0f, 328.4f};
    return h;
}

// ----------------------------------------------------------------- curves
/// Natural cubic spline through the points (like Krita / Photoshop curves),
/// clamped to 0..1. Same as ps_tools widgets.spline_eval().
inline void splineTable(std::vector<std::pair<float, float>> pts, float *out, int n)
{
    std::sort(pts.begin(), pts.end());
    const int m = int(pts.size());
    if (m < 2) {
        for (int i = 0; i < n; i++) out[i] = float(i) / float(n - 1);
        return;
    }
    std::vector<double> x(m), y(m), h(m, 0), al(m, 0), l(m, 1), mu(m, 0), z(m, 0), c(m, 0), b(m, 0), d(m, 0);
    for (int i = 0; i < m; i++) { x[i] = pts[i].first; y[i] = pts[i].second; }
    for (int i = 0; i < m - 1; i++) h[i] = std::max(1e-9, x[i + 1] - x[i]);
    for (int i = 1; i < m - 1; i++) al[i] = 3 / h[i] * (y[i + 1] - y[i]) - 3 / h[i - 1] * (y[i] - y[i - 1]);
    for (int i = 1; i < m - 1; i++) {
        l[i] = 2 * (x[i + 1] - x[i - 1]) - h[i - 1] * mu[i - 1];
        mu[i] = h[i] / l[i];
        z[i] = (al[i] - h[i - 1] * z[i - 1]) / l[i];
    }
    for (int j = m - 2; j >= 0; j--) {
        c[j] = z[j] - mu[j] * c[j + 1];
        b[j] = (y[j + 1] - y[j]) / h[j] - h[j] * (c[j + 1] + 2 * c[j]) / 3;
        d[j] = (c[j + 1] - c[j]) / (3 * h[j]);
    }
    int j = 0;
    for (int i = 0; i < n; i++) {
        const double xv = double(i) / double(n - 1);
        double v;
        if (xv <= x[0]) v = y[0];
        else if (xv >= x[m - 1]) v = y[m - 1];
        else {
            while (j < m - 2 && xv > x[j + 1]) j++;
            const double dx = xv - x[j];
            v = y[j] + b[j] * dx + c[j] * dx * dx + d[j] * dx * dx * dx;
        }
        out[i] = float(std::min(1.0, std::max(0.0, v)));
    }
}

static const int LUT_SIZE = 4096;

/// Value of a LUT over 0..1 (linear interpolation; above 1 the curve goes on
/// with the slope it has at the end, below 0 likewise at the start).
inline float lutAt(const float *lut, float x)
{
    if (x <= 0.0f) return lut[0] + x * (lut[1] - lut[0]) * (LUT_SIZE - 1);
    if (x >= 1.0f) return lut[LUT_SIZE - 1] + (x - 1.0f) * (lut[LUT_SIZE - 1] - lut[LUT_SIZE - 2]) * (LUT_SIZE - 1);
    const float f = x * (LUT_SIZE - 1);
    const int i = std::min(LUT_SIZE - 2, int(f));
    const float t = f - float(i);
    return lut[i] + (lut[i + 1] - lut[i]) * t;
}

/// The global tone curve of the Light panel (on sRGB-encoded values):
/// Contrast -> Whites / Blacks -> parametric curve -> point curve (RGB), then
/// the per channel point curves.
struct ToneLuts {
    std::vector<float> rgb[3];
    bool active = false;

    void build(const Settings &s) {
        active = s.toneActive();
        if (!active) return;
        std::vector<float> master(LUT_SIZE), pr(LUT_SIZE), pg(LUT_SIZE), pb(LUT_SIZE), prgb(LUT_SIZE);
        const float c = clampf(s.contrast / 100.0f, -1, 1);
        const float w = clampf(s.whites / 100.0f, -1, 1);
        const float bl = clampf(s.blacks / 100.0f, -1, 1);
        // parametric curve zones (Camera Raw: split points at 25 / 50 / 75)
        float s1 = clampf(s.pcSplit1 / 100.0f, 0.05f, 0.9f);
        float s2 = clampf(s.pcSplit2 / 100.0f, s1 + 0.02f, 0.95f);
        float s3 = clampf(s.pcSplit3 / 100.0f, s2 + 0.02f, 0.98f);
        const float zone[4][3] = {          // left, centre, right
            {0.0f, s1 * 0.5f, s2},
            {0.0f, (s1 + s2) * 0.5f, s3},
            {s1, (s2 + s3) * 0.5f, 1.0f},
            {s2, (s3 + 1.0f) * 0.5f, 1.0f},
        };
        const float amount[4] = {s.pcShadows / 100.0f, s.pcDarks / 100.0f, s.pcLights / 100.0f,
                                 s.pcHighlights / 100.0f};
        for (int i = 0; i < LUT_SIZE; i++) {
            float x = float(i) / float(LUT_SIZE - 1);
            // contrast: an S around the middle grey (in encoded values)
            x = x - c * 0.075f * std::sin(2.0f * kPi * x);
            // whites / blacks: move the ends of the range
            x = x * (1.0f + w * 0.25f * x * x * x * x);
            x = x + bl * 0.20f * (1.0f - std::min(1.0f, x)) * (1.0f - std::min(1.0f, x)) *
                    (1.0f - std::min(1.0f, x)) * (1.0f - std::min(1.0f, x));
            // parametric curve: smooth bumps in the four zones
            float y = x;
            for (int z = 0; z < 4; z++) {
                if (amount[z] == 0) continue;
                const float l = zone[z][0], m = zone[z][1], r = zone[z][2];
                float t;
                if (x <= l || x >= r) t = 0;
                else if (x < m) t = 0.5f - 0.5f * std::cos(kPi * (x - l) / std::max(1e-4f, m - l));
                else t = 0.5f + 0.5f * std::cos(kPi * (x - m) / std::max(1e-4f, r - m));
                y += amount[z] * 0.11f * t;
            }
            master[i] = y;
        }
        // keep it rising (strong combinations could fold it)
        for (int i = 1; i < LUT_SIZE; i++) master[i] = std::max(master[i], master[i - 1]);
        if (s.hasCurve(s.curveRgb)) {
            splineTable(s.curveRgb, prgb.data(), LUT_SIZE);
            for (int i = 0; i < LUT_SIZE; i++) master[i] = lutAt(prgb.data(), master[i]);
        }
        const std::vector<std::pair<float, float>> *per[3] = {&s.curveRed, &s.curveGreen, &s.curveBlue};
        std::vector<float> *tab[3] = {&pr, &pg, &pb};
        for (int ch = 0; ch < 3; ch++) {
            rgb[ch].resize(LUT_SIZE);
            const bool has = s.hasCurve(*per[ch]);
            if (has) splineTable(*per[ch], tab[ch]->data(), LUT_SIZE);
            for (int i = 0; i < LUT_SIZE; i++) {
                rgb[ch][i] = has ? lutAt(tab[ch]->data(), master[i]) : master[i];
            }
        }
    }
};

// ----------------------------------------------------------- image planes
/// Sums over a (2r+1)^2 window, cut at the edges of the buffer.
inline void boxSum(const float *src, float *dst, int w, int h, int r, std::vector<float> &tmp)
{
    tmp.resize(size_t(w) * h);
    std::vector<double> run;
    // horizontal
    run.resize(size_t(w) + 1);
    for (int y = 0; y < h; y++) {
        const float *s = src + size_t(y) * w;
        run[0] = 0;
        for (int x = 0; x < w; x++) run[x + 1] = run[x] + s[x];
        float *t = tmp.data() + size_t(y) * w;
        for (int x = 0; x < w; x++) {
            const int a = std::max(0, x - r), b = std::min(w - 1, x + r);
            t[x] = float(run[b + 1] - run[a]);
        }
    }
    // vertical
    run.resize(size_t(h) + 1);
    for (int x = 0; x < w; x++) {
        run[0] = 0;
        for (int y = 0; y < h; y++) run[y + 1] = run[y] + tmp[size_t(y) * w + x];
        for (int y = 0; y < h; y++) {
            const int a = std::max(0, y - r), b = std::min(h - 1, y + r);
            dst[size_t(y) * w + x] = float(run[b + 1] - run[a]);
        }
    }
}

/// Means weighted by the pixels' opacity (transparent pixels - e.g. outside
/// the image - do not darken the edges): mean(x) = box(w x) / box(w).
class Means {
public:
    Means(const std::vector<float> &weight, int w, int h) : m_w(weight), m_width(w), m_height(h) {}

    void mean(const float *src, float *dst, int r) {
        const size_t n = size_t(m_width) * m_height;
        std::vector<float> &den = denominator(r);
        m_buf.resize(n);
        for (size_t i = 0; i < n; i++) m_buf[i] = src[i] * m_w[i];
        boxSum(m_buf.data(), dst, m_width, m_height, r, m_tmp);
        for (size_t i = 0; i < n; i++) dst[i] = den[i] > 1e-6f ? dst[i] / den[i] : src[i];
    }

    /// Gaussian-like blur: three box means.
    void blur(const float *src, float *dst, const int rad[3]) {
        const size_t n = size_t(m_width) * m_height;
        std::vector<float> a(src, src + n);
        for (int k = 0; k < 3; k++) {
            mean(a.data(), dst, rad[k]);
            if (k < 2) std::copy(dst, dst + n, a.begin());
        }
    }

    /// Edge-aware smoothing (He et al. guided filter, the image guides itself).
    void guided(const float *I, float *q, int r, float eps) {
        guided(I, I, q, r, eps);
    }

    /// Guided filter: smooths p, keeping the edges of the guide I.
    void guided(const float *I, const float *p, float *q, int r, float eps) {
        const size_t n = size_t(m_width) * m_height;
        std::vector<float> mI(n), mp(n), mIp(n), mII(n), t(n), a(n), b(n);
        mean(I, mI.data(), r);
        mean(p, mp.data(), r);
        for (size_t i = 0; i < n; i++) t[i] = I[i] * p[i];
        mean(t.data(), mIp.data(), r);
        for (size_t i = 0; i < n; i++) t[i] = I[i] * I[i];
        mean(t.data(), mII.data(), r);
        for (size_t i = 0; i < n; i++) {
            const float var = std::max(0.0f, mII[i] - mI[i] * mI[i]);
            const float cov = mIp[i] - mI[i] * mp[i];
            a[i] = cov / (var + eps);
            b[i] = mp[i] - a[i] * mI[i];
        }
        mean(a.data(), mI.data(), r);
        mean(b.data(), mp.data(), r);
        for (size_t i = 0; i < n; i++) q[i] = mI[i] * I[i] + mp[i];
    }

    /// Local minimum over a (2r+1)^2 window (transparent pixels ignored).
    void minimum(const float *src, float *dst, int r) {
        const size_t n = size_t(m_width) * m_height;
        std::vector<float> t(n);
        const float big = 1e30f;
        for (int y = 0; y < m_height; y++) {
            for (int x = 0; x < m_width; x++) {
                float v = big;
                const int a = std::max(0, x - r), b = std::min(m_width - 1, x + r);
                for (int k = a; k <= b; k++) {
                    const size_t i = size_t(y) * m_width + k;
                    if (m_w[i] > 0) v = std::min(v, src[i]);
                }
                t[size_t(y) * m_width + x] = v;
            }
        }
        for (int y = 0; y < m_height; y++) {
            const int a = std::max(0, y - r), b = std::min(m_height - 1, y + r);
            for (int x = 0; x < m_width; x++) {
                float v = big;
                for (int k = a; k <= b; k++) v = std::min(v, t[size_t(k) * m_width + x]);
                const size_t i = size_t(y) * m_width + x;
                dst[i] = v >= big ? src[i] : v;
            }
        }
    }

private:
    std::vector<float> &denominator(int r) {
        for (auto &d : m_den) {
            if (d.first == r) return d.second;
        }
        m_den.push_back(std::make_pair(r, std::vector<float>(size_t(m_width) * m_height)));
        std::vector<float> &den = m_den.back().second;
        boxSum(m_w.data(), den.data(), m_width, m_height, r, m_tmp);
        return den;
    }

    const std::vector<float> &m_w;
    int m_width, m_height;
    std::vector<std::pair<int, std::vector<float>>> m_den;
    std::vector<float> m_buf, m_tmp;
};

// ------------------------------------------------------------------ grain
inline uint32_t hash2(int32_t x, int32_t y, uint32_t seed)
{
    uint32_t h = uint32_t(x) * 0x8da6b343u ^ uint32_t(y) * 0xd8163841u ^ seed * 0xcb1ab31fu;
    h ^= h >> 13;
    h *= 0x5bd1e995u;
    h ^= h >> 15;
    return h;
}

inline float hashUnit(int32_t x, int32_t y, uint32_t seed)
{
    return float(hash2(x, y, seed) & 0xffffffu) / float(0xffffff) * 2.0f - 1.0f;   // -1 .. 1
}

/// Smooth value noise, -1..1, cells of the given size (pixels, absolute
/// coordinates: the same grain whatever the tile).
inline float valueNoise(float x, float y, float cell, uint32_t seed)
{
    const float fx = x / cell, fy = y / cell;
    const int32_t ix = int32_t(std::floor(fx)), iy = int32_t(std::floor(fy));
    float tx = fx - float(ix), ty = fy - float(iy);
    tx = tx * tx * (3 - 2 * tx);
    ty = ty * ty * (3 - 2 * ty);
    const float a = hashUnit(ix, iy, seed), b = hashUnit(ix + 1, iy, seed);
    const float c = hashUnit(ix, iy + 1, seed), d = hashUnit(ix + 1, iy + 1, seed);
    return (a + (b - a) * tx) + ((c + (d - c) * tx) - (a + (b - a) * tx)) * ty;
}

// ---------------------------------------------------------------- process
/// The buffer: planar linear RGB + alpha (alpha is not changed), x0 / y0 =
/// position of the buffer's top-left pixel in the image (full-resolution
/// pixels / 2^lod), image = the image's rectangle (for the vignette).
struct Buffer {
    int width = 0, height = 0;
    int x0 = 0, y0 = 0;
    std::vector<float> r, g, b, a;
};

struct Frame {
    int x = 0, y = 0, width = 1, height = 1;   // the image, at this level of detail
    int lod = 0;
};

namespace detail {

inline void toneLocal(const Settings &s, const Radii &rad, Buffer &buf, Means &means, std::vector<float> &Y)
{
    const size_t n = size_t(buf.width) * buf.height;
    // perceptual lightness (sRGB-encoded luminance)
    std::vector<float> P(n), P0(n), base(n);
    for (size_t i = 0; i < n; i++) {
        Y[i] = std::max(0.0f, luminance(buf.r[i], buf.g[i], buf.b[i]));
        P[i] = srgbEncode(Y[i]);
        P0[i] = P[i];
    }
    if (s.highlights != 0 || s.shadows != 0) {
        const float hl = clampf(s.highlights / 100.0f, -1, 1);
        const float sh = clampf(s.shadows / 100.0f, -1, 1);
        means.guided(P.data(), base.data(), rad.toneGuide, 0.02f);
        for (size_t i = 0; i < n; i++) {
            const float bb = clampf(base[i], 0.0f, 1.0f);
            const float lo = std::sqrt(bb) * (1 - bb) * (1 - bb) * (1 - bb);    // dark areas
            // bright areas; above 1 (over-exposed) Highlights pulls them back
            const float hi = 0.45f * bb * bb * smoothstep(0.2f, 0.9f, bb) +
                             0.6f * std::max(0.0f, base[i] - 1.0f);
            P[i] += sh * 0.62f * lo + hl * 0.85f * hi;
        }
    }
    if (s.clarity != 0) {
        const float c = clampf(s.clarity / 100.0f, -1, 1);
        means.guided(P.data(), base.data(), rad.clarityGuide, 0.008f);
        for (size_t i = 0; i < n; i++) {
            const float mb = clampf(base[i], 0.0f, 1.0f);
            const float mid = 1.0f - (2 * mb - 1) * (2 * mb - 1);       // midtones count most
            const float d = P[i] - base[i];
            P[i] += (c > 0 ? 1.6f : 0.9f) * c * (0.35f + 0.65f * mid) * d;
        }
    }
    if (s.texture != 0) {
        const float t = clampf(s.texture / 100.0f, -1, 1);
        means.blur(P.data(), base.data(), rad.textureBlur);
        for (size_t i = 0; i < n; i++) {
            float d = P[i] - base[i];
            // fine texture, not edges: big differences count less
            d = d / (1.0f + std::fabs(d) * 12.0f);
            P[i] += (t > 0 ? 2.2f : 1.0f) * t * d;
        }
    }
    // back to linear: scale the colour (keeps hue and saturation)
    for (size_t i = 0; i < n; i++) {
        if (P[i] == P0[i]) continue;
        const float Yn = srgbDecode(std::max(-0.05f, P[i]));
        if (Y[i] > 1e-5f) {
            const float k = std::max(0.0f, Yn) / Y[i];
            buf.r[i] *= k;
            buf.g[i] *= k;
            buf.b[i] *= k;
        } else {
            const float d = Yn - Y[i];
            buf.r[i] += d;
            buf.g[i] += d;
            buf.b[i] += d;
        }
    }
}

inline void dehaze(const Settings &s, const Radii &rad, Buffer &buf, Means &means)
{
    const size_t n = size_t(buf.width) * buf.height;
    const float amount = clampf(s.dehaze / 100.0f, -1, 1);
    std::vector<float> dark(n), local(n), guide(n), t(n);
    for (size_t i = 0; i < n; i++) {
        dark[i] = std::max(0.0f, std::min(buf.r[i], std::min(buf.g[i], buf.b[i])));
        guide[i] = srgbEncode(std::max(0.0f, luminance(buf.r[i], buf.g[i], buf.b[i])));
    }
    means.minimum(dark.data(), local.data(), rad.dehazeMin);
    means.guided(guide.data(), local.data(), t.data(), rad.dehazeGuide, 0.002f);
    const float A = 0.96f;          // air light: a bright, nearly white sky
    for (size_t i = 0; i < n; i++) {
        const float haze = clampf(t[i] / A, 0.0f, 1.0f);
        if (amount > 0) {
            const float tr = std::max(0.2f, 1.0f - 0.95f * amount * haze);
            buf.r[i] = (buf.r[i] - A) / tr + A;
            buf.g[i] = (buf.g[i] - A) / tr + A;
            buf.b[i] = (buf.b[i] - A) / tr + A;
            // haze removal darkens: give back a little of the brightness
            const float lift = 1.0f + 0.08f * amount * haze;
            buf.r[i] = std::max(0.0f, buf.r[i]) * lift;
            buf.g[i] = std::max(0.0f, buf.g[i]) * lift;
            buf.b[i] = std::max(0.0f, buf.b[i]) * lift;
        } else {
            const float k = -amount * 0.45f * (0.45f + 0.55f * (1.0f - haze));
            buf.r[i] += (A * 0.92f - buf.r[i]) * k;
            buf.g[i] += (A * 0.94f - buf.g[i]) * k;
            buf.b[i] += (A - buf.b[i]) * k;
        }
    }
}

inline void noiseReduction(const Settings &s, const Radii &rad, std::vector<float> &L, std::vector<float> &A,
                           std::vector<float> &B, Means &means)
{
    const size_t n = L.size();
    std::vector<float> sm(n);
    if (s.nrLum > 0) {
        const float amount = clampf(s.nrLum / 100.0f, 0, 1);
        const float keep = clampf(s.nrLumDetail / 100.0f, 0, 1);
        const float contrast = clampf(s.nrLumContrast / 100.0f, 0, 1);
        const float e = 0.004f + amount * 0.05f * (1.15f - keep);
        means.guided(L.data(), sm.data(), rad.nrLum, e * e);
        for (size_t i = 0; i < n; i++) {
            const float d = L[i] - sm[i];
            L[i] = sm[i] + d * (1.0f - amount * (0.85f - 0.45f * keep)) + d * contrast * 0.25f * amount;
        }
    }
    if (s.nrColor > 0) {
        const float amount = clampf(s.nrColor / 100.0f, 0, 1);
        const float keep = clampf(s.nrColorDetail / 100.0f, 0, 1);
        const float e = 0.01f + 0.08f * (1.0f - keep);
        std::vector<float> sa(n);
        means.guided(L.data(), A.data(), sa.data(), rad.nrColor, e * e);
        means.guided(L.data(), B.data(), sm.data(), rad.nrColor, e * e);
        for (size_t i = 0; i < n; i++) {
            A[i] += (sa[i] - A[i]) * amount;
            B[i] += (sm[i] - B[i]) * amount;
        }
    }
}

/// The two Color Mixer colours a hue falls between and their weights.
inline void mixerWeights(float h, int &i0, int &i1, float &w0, float &w1)
{
    const float *hs = mixerHues();
    i0 = 7;
    i1 = 0;
    for (int i = 0; i < 8; i++) {
        const int j = (i + 1) % 8;
        const float a = hs[i], b = j ? hs[j] : hs[j] + 360.0f;
        float hh = h;
        if (i == 7 && hh < hs[0]) hh += 360.0f;
        if (hh >= a && hh < b) { i0 = i; i1 = j; break; }
    }
    const float a = hs[i0], b = i1 ? hs[i1] : hs[i1] + 360.0f;
    float hh = h;
    if (i0 == 7 && hh < hs[0]) hh += 360.0f;
    const float t = clampf((hh - a) / std::max(1e-3f, b - a), 0, 1);
    w0 = 1 - t;
    w1 = t;
}

inline void grade(const Settings &s, float &L, float &A, float &B);

inline void color(const Settings &s, float &L, float &A, float &B)
{
    float C = std::sqrt(A * A + B * B);
    if (s.bw != 0) {
        // Black & White: the B&W Mix (the mixer's luminance sliders) sets how
        // light each colour turns; then the colour goes, toning (Color
        // Grading) still applies
        bool mix = false;
        for (int i = 0; i < 8; i++) mix = mix || s.mixLum[i] != 0;
        if (mix && C > 1e-4f) {
            float h = std::atan2(B, A) * (180.0f / kPi);
            if (h < 0) h += 360.0f;
            int i0, i1;
            float w0, w1;
            mixerWeights(h, i0, i1, w0, w1);
            const float lum = (w0 * s.mixLum[i0] + w1 * s.mixLum[i1]) / 100.0f;
            const float chroma = clampf(C / 0.10f, 0, 1);
            L += lum * 0.35f * chroma * (lum > 0 ? (1.0f - L) : L) * 1.6f;
        }
        A = 0;
        B = 0;
        grade(s, L, A, B);
        return;
    }
    bool mixerOn = false;
    for (int i = 0; i < 8 && !mixerOn; i++) {
        mixerOn = s.mixHue[i] != 0 || s.mixSat[i] != 0 || s.mixLum[i] != 0;
    }
    if (!mixerOn && s.vibrance == 0 && s.saturation != 0) {
        // saturation only: no hue needed
        const float k = std::max(0.0f, 1.0f + clampf(s.saturation / 100.0f, -1, 1));
        A *= k;
        B *= k;
        C *= k;
    }
    float h = 0;
    if (mixerOn || s.vibrance != 0) {
        h = std::atan2(B, A) * (180.0f / kPi);
        if (h < 0) h += 360.0f;
    }
    bool changed = false;

    // Color Mixer: weights of the two nearest colours (they add up to 1)
    if (mixerOn && C > 1e-4f) {
        int i0, i1;
        float w0, w1;
        mixerWeights(h, i0, i1, w0, w1);
        const float hueShift = (w0 * s.mixHue[i0] + w1 * s.mixHue[i1]) / 100.0f;
        const float satShift = (w0 * s.mixSat[i0] + w1 * s.mixSat[i1]) / 100.0f;
        const float lumShift = (w0 * s.mixLum[i0] + w1 * s.mixLum[i1]) / 100.0f;
        const float chroma = clampf(C / 0.12f, 0, 1);       // greys are not touched
        h += hueShift * 30.0f * chroma;
        C *= std::max(0.0f, 1.0f + satShift * (satShift > 0 ? 0.9f : 1.0f));
        L += lumShift * 0.22f * chroma * (lumShift > 0 ? (1.0f - L) : L) * 1.6f;
        changed = true;
    }
    if (s.saturation != 0 && (mixerOn || s.vibrance != 0)) {
        C *= std::max(0.0f, 1.0f + clampf(s.saturation / 100.0f, -1, 1));
        changed = true;
    }
    if (s.vibrance != 0) {
        const float v = clampf(s.vibrance / 100.0f, -1, 1);
        const float low = 1.0f - clampf(C / 0.28f, 0, 1);              // the less saturated, the more
        // skin tones (orange) are protected from a vibrance boost
        float dh = std::fabs(h - 55.0f);
        if (dh > 180) dh = 360 - dh;
        const float skin = v > 0 ? 1.0f - 0.55f * (1.0f - smoothstep(0, 35, dh)) : 1.0f;
        C *= std::max(0.0f, 1.0f + v * (v > 0 ? 1.4f : 0.9f) * low * low * skin);
        changed = true;
    }
    if (changed) {
        const float hr = h * (kPi / 180.0f);
        A = C * std::cos(hr);
        B = C * std::sin(hr);
    }
    grade(s, L, A, B);
}

/// Color Grading (split toning) by lightness zones.
inline void grade(const Settings &s, float &L, float &A, float &B)
{
    bool grading = false;
    for (int z = 0; z < 4; z++) {
        if (s.cgSat[z] != 0 || s.cgLum[z] != 0) grading = true;
    }
    if (grading) {
        const float blend = clampf(s.cgBlending / 100.0f, 0, 1);
        const float bal = clampf(s.cgBalance / 100.0f, -1, 1);
        const float width = 0.08f + 0.55f * blend;
        const float m1 = 0.36f - 0.14f * bal, m2 = 0.70f - 0.14f * bal;
        const float Lc = clampf(L, 0, 1);
        const float ws = 1.0f - smoothstep(m1 - width * 0.5f, m1 + width * 0.5f, Lc);
        const float wh = smoothstep(m2 - width * 0.5f, m2 + width * 0.5f, Lc);
        const float wm = clampf(1.0f - ws - wh, 0, 1);
        const float wz[4] = {ws, wm, wh, 1.0f};
        for (int z = 0; z < 4; z++) {
            if (wz[z] <= 0) continue;
            if (s.cgSat[z] != 0) {
                float da, db;
                hueDirection(s.cgHue[z], da, db);
                const float k = clampf(s.cgSat[z] / 100.0f, 0, 1) * 0.07f * wz[z];
                A += da * k;
                B += db * k;
            }
            if (s.cgLum[z] != 0) {
                L += clampf(s.cgLum[z] / 100.0f, -1, 1) * 0.16f * wz[z];
            }
        }
    }
}

inline void vignette(const Settings &s, Buffer &buf, const Frame &fr, size_t i, int x, int y)
{
    const float amount = clampf(s.vigAmount / 100.0f, -1, 1);
    const float cx = fr.x + fr.width * 0.5f, cy = fr.y + fr.height * 0.5f;
    float u = (x + 0.5f - cx) / (fr.width * 0.5f), v = (y + 0.5f - cy) / (fr.height * 0.5f);
    const float round = clampf(s.vigRoundness / 100.0f, -1, 1);
    float d;
    if (round >= 0) {
        // 0: an ellipse with the image's proportions, 100: a circle
        const float m = float(std::min(fr.width, fr.height));
        const float uc = (x + 0.5f - cx) / (m * 0.5f), vc = (y + 0.5f - cy) / (m * 0.5f);
        const float de = std::sqrt(u * u + v * v), dc = std::sqrt(uc * uc + vc * vc) * 0.82f;
        d = de + (dc - de) * round;
    } else {
        // towards a rectangle with rounded corners
        const float p = 2.0f + (-round) * 8.0f;
        d = std::pow(std::pow(std::fabs(u), p) + std::pow(std::fabs(v), p), 1.0f / p);
    }
    const float mid = clampf(s.vigMidpoint / 100.0f, 0, 1);
    const float feather = clampf(s.vigFeather / 100.0f, 0, 1);
    const float r0 = 0.25f + mid * 0.95f;
    const float w = 0.04f + feather * 1.1f;
    const float f = smoothstep(r0 - w * 0.5f, r0 + w * 0.5f, d) * std::fabs(amount);
    if (f <= 0) return;
    float &R = buf.r[i], &G = buf.g[i], &B = buf.b[i];
    if (s.vigStyle == 2) {                       // paint overlay
        const float t = amount < 0 ? 0.0f : 1.0f;
        const float k = f * 0.85f;
        R += (t - R) * k;
        G += (t - G) * k;
        B += (t - B) * k;
        return;
    }
    const float Y = std::max(0.0f, luminance(R, G, B));
    float protect = 1.0f;
    if (amount < 0 && s.vigHighlights > 0) {
        protect = 1.0f - clampf(s.vigHighlights / 100.0f, 0, 1) * smoothstep(0.35f, 0.9f, srgbEncode(Y));
    }
    if (s.vigStyle == 0) {                       // highlight priority: like exposure
        const float k = amount < 0 ? std::pow(2.0f, -2.2f * f * protect) : 1.0f;
        if (amount < 0) {
            R *= k;
            G *= k;
            B *= k;
        } else {
            const float t = f * 0.75f;           // brighter, towards white
            R += (1 - R) * t;
            G += (1 - G) * t;
            B += (1 - B) * t;
        }
    } else {                                     // colour priority: lightness only
        float L, A, Bb;
        toOklab(R, G, B, L, A, Bb);
        L = amount < 0 ? L * (1.0f - 0.7f * f * protect) : L + (1.0f - L) * 0.7f * f;
        fromOklab(L, A, Bb, R, G, B);
    }
}

} // namespace detail

/// Applies the settings to the buffer (in place). The area at least
/// border(settings, lod) pixels away from the buffer's edges is exact.
inline void process(const Settings &s, Buffer &buf, const Frame &fr, const ToneLuts &tone)
{
    const int lod = fr.lod;
    const Radii rad = radii(s, lod);
    const size_t n = size_t(buf.width) * buf.height;
    if (!n) return;

    std::vector<float> weight(n);
    for (size_t i = 0; i < n; i++) weight[i] = clampf(buf.a[i], 0.0f, 1.0f);
    Means means(weight, buf.width, buf.height);

    // ---- noise reduction (first, on the input)
    if (s.nrLum > 0 || s.nrColor > 0) {
        std::vector<float> L(n), A(n), B(n);
        for (size_t i = 0; i < n; i++) toOklab(buf.r[i], buf.g[i], buf.b[i], L[i], A[i], B[i]);
        detail::noiseReduction(s, rad, L, A, B, means);
        for (size_t i = 0; i < n; i++) fromOklab(L[i], A[i], B[i], buf.r[i], buf.g[i], buf.b[i]);
    }

    // ---- white balance, exposure (linear light)
    if (s.temperature != 0 || s.tint != 0 || s.exposure != 0) {
        const float t = clampf(s.temperature / 100.0f, -1, 1), ti = clampf(s.tint / 100.0f, -1, 1);
        float kr = std::pow(2.0f, 0.6f * t), kg = std::pow(2.0f, -0.36f * ti), kb = std::pow(2.0f, -0.6f * t);
        const float norm = luminance(kr, kg, kb);
        const float e = std::pow(2.0f, clampf(s.exposure, -5, 5));
        kr = kr / norm * e;
        kg = kg / norm * e;
        kb = kb / norm * e;
        for (size_t i = 0; i < n; i++) {
            buf.r[i] *= kr;
            buf.g[i] *= kg;
            buf.b[i] *= kb;
        }
    }

    // ---- dehaze
    if (s.dehaze != 0) detail::dehaze(s, rad, buf, means);

    // ---- highlights / shadows / clarity / texture (local, on lightness)
    if (s.highlights != 0 || s.shadows != 0 || s.clarity != 0 || s.texture != 0) {
        std::vector<float> Y(n);
        detail::toneLocal(s, rad, buf, means, Y);
    }

    // ---- tone curve (contrast, whites, blacks, parametric and point curves)
    if (tone.active) {
        for (size_t i = 0; i < n; i++) {
            buf.r[i] = srgbDecode(lutAt(tone.rgb[0].data(), srgbEncode(std::max(0.0f, buf.r[i]))));
            buf.g[i] = srgbDecode(lutAt(tone.rgb[1].data(), srgbEncode(std::max(0.0f, buf.g[i]))));
            buf.b[i] = srgbDecode(lutAt(tone.rgb[2].data(), srgbEncode(std::max(0.0f, buf.b[i]))));
        }
    }

    // ---- colour (vibrance, saturation, mixer, grading), sharpening, grain
    const bool col = s.colorActive();
    const bool sharpen = s.sharpAmount > 0;
    const bool grain = s.grainAmount > 0;
    if (col || sharpen || grain) {
        std::vector<float> L(n), A(n), B(n);
        for (size_t i = 0; i < n; i++) {
            toOklab(std::max(0.0f, buf.r[i]), std::max(0.0f, buf.g[i]), std::max(0.0f, buf.b[i]),
                            L[i], A[i], B[i]);
            if (col) detail::color(s, L[i], A[i], B[i]);
        }
        if (sharpen) {
            std::vector<float> bl(n);
            means.blur(L.data(), bl.data(), rad.sharpenBlur);
            const float amount = clampf(s.sharpAmount / 100.0f, 0, 1.5f) * 1.25f;
            const float det = clampf(s.sharpDetail / 100.0f, 0, 1);
            const float limit = 0.02f + 0.30f * det * det;       // low Detail: no halos
            const float mask = clampf(s.sharpMasking / 100.0f, 0, 1);
            const int W = buf.width, H = buf.height;
            std::vector<float> out(L);
            for (int y = 0; y < H; y++) {
                for (int x = 0; x < W; x++) {
                    const size_t i = size_t(y) * W + x;
                    float d = L[i] - bl[i];
                    d = d / (1.0f + std::fabs(d) / limit);
                    float m = 1.0f;
                    if (mask > 0) {
                        // Masking: only edges (gradient of the blurred image)
                        const int xl = std::max(0, x - 1), xr = std::min(W - 1, x + 1);
                        const int yu = std::max(0, y - 1), yd = std::min(H - 1, y + 1);
                        const float gx = bl[size_t(y) * W + xr] - bl[size_t(y) * W + xl];
                        const float gy = bl[size_t(yd) * W + x] - bl[size_t(yu) * W + x];
                        const float g = std::sqrt(gx * gx + gy * gy);
                        const float t0 = mask * 0.06f;
                        m = smoothstep(t0 * 0.5f, t0 * 1.5f + 1e-4f, g);
                    }
                    out[i] = L[i] + amount * d * m;
                }
            }
            L.swap(out);
        }
        if (grain) {
            const float amount = clampf(s.grainAmount / 100.0f, 0, 1);
            const float size = clampf(s.grainSize / 100.0f, 0, 1);
            const float rough = clampf(s.grainRoughness / 100.0f, 0, 1);
            const float scale = float(1 << std::max(0, std::min(lod, 8)));
            const float cell = 0.7f + size * 3.5f;                       // full-resolution pixels
            for (int y = 0; y < buf.height; y++) {
                for (int x = 0; x < buf.width; x++) {
                    const size_t i = size_t(y) * buf.width + x;
                    const float fx = (buf.x0 + x) * scale, fy = (buf.y0 + y) * scale;
                    float g = valueNoise(fx, fy, cell, 17u);
                    const float g2 = valueNoise(fx + 31.7f, fy - 11.3f, cell * 2.7f, 91u);
                    g = g * (1.0f - 0.55f * rough) + g2 * 0.75f * rough;
                    const float Lc = clampf(L[i], 0, 1);
                    const float mid = 0.25f + 3.0f * Lc * (1 - Lc);
                    // at a lower level of detail many grains fall in one pixel
                    const float k = amount * 0.075f * mid / std::sqrt(std::max(1.0f, scale * scale / (cell * cell)));
                    L[i] += g * k;
                }
            }
        }
        for (size_t i = 0; i < n; i++) fromOklab(L[i], A[i], B[i], buf.r[i], buf.g[i], buf.b[i]);
    }

    // ---- vignette (post-crop: the whole image)
    if (s.vigAmount != 0) {
        for (int y = 0; y < buf.height; y++) {
            for (int x = 0; x < buf.width; x++) {
                const size_t i = size_t(y) * buf.width + x;
                detail::vignette(s, buf, fr, i, buf.x0 + x, buf.y0 + y);
            }
        }
    }
}

} // namespace cr
} // namespace ps

#endif
