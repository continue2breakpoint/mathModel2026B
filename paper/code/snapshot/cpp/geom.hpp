#pragma once

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
#include <optional>
#include <random>
#include <stdexcept>
#include <utility>
#include <vector>

namespace geom {

constexpr double PI = 3.141592653589793238462643383279502884;
constexpr double DEG2RAD = PI / 180.0;
constexpr double RAD2DEG = 180.0 / PI;

struct Vec2 {
    double x = 0.0;
    double y = 0.0;
};

inline Vec2 operator+(const Vec2& a, const Vec2& b) { return {a.x + b.x, a.y + b.y}; }
inline Vec2 operator-(const Vec2& a, const Vec2& b) { return {a.x - b.x, a.y - b.y}; }
inline Vec2 operator*(double s, const Vec2& a) { return {s * a.x, s * a.y}; }
inline Vec2 operator*(const Vec2& a, double s) { return {s * a.x, s * a.y}; }
inline Vec2 operator/(const Vec2& a, double s) { return {a.x / s, a.y / s}; }
inline double dot(const Vec2& a, const Vec2& b) { return a.x * b.x + a.y * b.y; }
inline double cross(const Vec2& a, const Vec2& b) { return a.x * b.y - a.y * b.x; }
inline double cross(const Vec2& o, const Vec2& a, const Vec2& b) {
    return cross(a - o, b - o);
}
inline double norm2(const Vec2& a) { return dot(a, a); }
inline double norm(const Vec2& a) { return std::sqrt(norm2(a)); }
inline double dist(const Vec2& a, const Vec2& b) { return norm(a - b); }

inline double wrap360(double deg) {
    double r = std::fmod(deg, 360.0);
    if (r < 0.0) r += 360.0;
    return r;
}

inline double wrap180(double deg) {
    double r = std::fmod(deg + 180.0, 360.0);
    if (r < 0.0) r += 360.0;
    return r - 180.0;
}

inline Vec2 unit_deg(double deg) {
    double r = deg * DEG2RAD;
    return {std::cos(r), std::sin(r)};
}

inline double bearing_deg(const Vec2& from, const Vec2& to) {
    return wrap360(std::atan2(to.y - from.y, to.x - from.x) * RAD2DEG);
}

inline double angle_diff_deg(double a, double b) {
    return std::abs(wrap180(a - b));
}

struct Observation {
    Vec2 point;
    double bearing_deg = 0.0;
    double half_width_deg = 1.0;
    double max_range_m = 1500.0;
};

struct HalfPlane {
    // a*x + b*y + c >= 0
    double a = 0.0;
    double b = 0.0;
    double c = 0.0;
};

struct ConvexRegion {
    std::vector<Vec2> vertices;  // CCW when bounded and non-empty
    bool bounded = true;
    bool empty = false;
    std::uint64_t version = 0;

    bool is_empty() const { return empty || vertices.size() < 3; }
};

struct DiameterWitness {
    Vec2 a;
    Vec2 b;
    double distance = 0.0;
};

struct CircleResult {
    Vec2 center;
    double radius = 0.0;
    std::vector<Vec2> basis;  // 1, 2 or 3 points

    int basis_size() const { return static_cast<int>(basis.size()); }
};

// ---------------------------------------------------------------------------
// Basic polygon helpers.
// ---------------------------------------------------------------------------
inline std::vector<Vec2> polygon_circle(Vec2 center, double radius, int sides) {
    if (sides < 8) sides = 8;
    const double r = radius / std::cos(PI / sides);  // circumscribed polygon
    std::vector<Vec2> out;
    out.reserve(static_cast<std::size_t>(sides));
    for (int k = 0; k < sides; ++k) {
        const double a = 2.0 * PI * static_cast<double>(k) / static_cast<double>(sides);
        out.push_back({center.x + r * std::cos(a), center.y + r * std::sin(a)});
    }
    return out;
}

inline std::vector<Vec2> clip_halfplane(const std::vector<Vec2>& poly,
                                        const HalfPlane& hp,
                                        double eps = 1e-10) {
    if (poly.empty()) return {};
    std::vector<Vec2> out;
    out.reserve(poly.size() + 1);
    const std::size_t n = poly.size();
    for (std::size_t i = 0; i < n; ++i) {
        const Vec2 cur = poly[i];
        const Vec2 nxt = poly[(i + 1) % n];
        const double dc = hp.a * cur.x + hp.b * cur.y + hp.c;
        const double dn = hp.a * nxt.x + hp.b * nxt.y + hp.c;
        if (dc >= -eps) out.push_back(cur);
        if ((dc > eps && dn < -eps) || (dc < -eps && dn > eps)) {
            const double t = dc / (dc - dn);
            out.push_back({cur.x + (nxt.x - cur.x) * t,
                           cur.y + (nxt.y - cur.y) * t});
        }
    }
    return out;
}

inline std::pair<HalfPlane, HalfPlane> wedge_halfplanes(const Observation& obs) {
    const Vec2 p = obs.point;
    const double eps = obs.half_width_deg;

    // inside: cross(u(theta+eps), Q-P) <= 0
    Vec2 u = unit_deg(obs.bearing_deg + eps);
    HalfPlane h1{ u.y, -u.x, -u.y * p.x + u.x * p.y };

    // inside: cross(u(theta-eps), Q-P) >= 0
    u = unit_deg(obs.bearing_deg - eps);
    HalfPlane h2{ -u.y, u.x, u.y * p.x - u.x * p.y };

    return {h1, h2};
}

inline ConvexRegion build_region(const std::vector<Observation>& observations,
                                 double bound_radius = 1800.0,
                                 int bound_sides = 96) {
    std::vector<Vec2> poly = polygon_circle({0.0, 0.0}, bound_radius, bound_sides);
    for (const Observation& obs : observations) {
        const auto [h1, h2] = wedge_halfplanes(obs);
        poly = clip_halfplane(poly, h1);
        if (poly.size() < 3) return ConvexRegion{{}, true, true, 0};
        poly = clip_halfplane(poly, h2);
        if (poly.size() < 3) return ConvexRegion{{}, true, true, 0};
    }
    return ConvexRegion{std::move(poly), true, false, 0};
}

inline std::vector<Vec2> intersect_convex_polygons(
    const std::vector<Vec2>& subject,
    const std::vector<Vec2>& clip) {
    if (subject.size() < 3 || clip.size() < 3) return {};
    std::vector<Vec2> out = subject;
    const std::size_t n = clip.size();
    for (std::size_t i = 0; i < n; ++i) {
        const Vec2 a = clip[i];
        const Vec2 b = clip[(i + 1) % n];
        const double dx = b.x - a.x;
        const double dy = b.y - a.y;
        // Keep points on the left of directed edge a->b.
        const HalfPlane hp{-dy, dx, dy * a.x - dx * a.y};
        out = clip_halfplane(out, hp);
        if (out.size() < 3) return {};
    }
    return out;
}

inline ConvexRegion clip_region_by_disk(const ConvexRegion& region,
                                        const Vec2& center,
                                        double radius,
                                        int sides = 240) {
    if (region.is_empty()) return region;
    const auto disk_poly = polygon_circle(center, radius, sides);
    auto out = intersect_convex_polygons(region.vertices, disk_poly);
    if (out.size() < 3) return ConvexRegion{{}, true, true, 0};
    return ConvexRegion{std::move(out), true, false, 0};
}

inline bool point_in_region(const Vec2& q, const ConvexRegion& region, double eps = 1e-8) {
    if (region.is_empty()) return false;
    const auto& p = region.vertices;
    const std::size_t n = p.size();
    for (std::size_t i = 0; i < n; ++i) {
        const Vec2 a = p[i];
        const Vec2 b = p[(i + 1) % n];
        if (cross(a, b, q) < -eps) return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Diameter: exhaustive reference and O(m) rotating calipers.
// ---------------------------------------------------------------------------
inline DiameterWitness brute_force_diameter(const ConvexRegion& region) {
    DiameterWitness best{{0.0, 0.0}, {0.0, 0.0}, -1.0};
    const auto& p = region.vertices;
    for (std::size_t i = 0; i < p.size(); ++i) {
        for (std::size_t j = i + 1; j < p.size(); ++j) {
            const double d = dist(p[i], p[j]);
            if (d > best.distance) best = {p[i], p[j], d};
        }
    }
    return best;
}

inline DiameterWitness rotating_calipers_diameter(const ConvexRegion& region) {
    const auto& p = region.vertices;
    const std::size_t n = p.size();
    if (n == 0) return {{0.0, 0.0}, {0.0, 0.0}, 0.0};
    if (n == 1) return {p[0], p[0], 0.0};
    if (n == 2) return {p[0], p[1], dist(p[0], p[1])};

    auto area2 = [&](std::size_t ia, std::size_t ib, std::size_t ic) -> double {
        return std::abs(cross(p[ia], p[ib], p[ic]));
    };

    std::size_t k = 1;
    while (area2(n - 1, 0, (k + 1) % n) > area2(n - 1, 0, k)) {
        k = (k + 1) % n;
    }

    DiameterWitness best{p[0], p[k], dist(p[0], p[k])};
    std::size_t i = 0;
    std::size_t j = k;
    std::size_t guard = 0;
    const std::size_t guard_limit = 4 * n + 16;

    while (i < n && guard < guard_limit) {
        ++guard;
        while (area2(i, (i + 1) % n, (j + 1) % n) > area2(i, (i + 1) % n, j)) {
            j = (j + 1) % n;
        }
        const std::size_t pairs[4][2] = {
            {i, j},
            {(i + 1) % n, j},
            {i, (j + 1) % n},
            {(i + 1) % n, (j + 1) % n},
        };
        for (const auto& pr : pairs) {
            const double d = dist(p[pr[0]], p[pr[1]]);
            if (d > best.distance) best = {p[pr[0]], p[pr[1]], d};
        }
        ++i;
    }
    return best;
}

inline bool diameter_circle_covers(const ConvexRegion& region,
                                   const DiameterWitness& diameter,
                                   double eps = 1e-9) {
    if (region.is_empty()) return true;
    const Vec2 a = diameter.a;
    const Vec2 b = diameter.b;
    for (const Vec2& v : region.vertices) {
        if (dot(v - a, v - b) > eps) return false;
    }
    return true;
}

inline bool can_clear(const CircleResult& circle, double tolerance_m = 20.0) {
    return circle.radius <= tolerance_m + 1e-9;
}

// ---------------------------------------------------------------------------
// Minimum enclosing circle: brute force reference + Welzl.
// ---------------------------------------------------------------------------
inline bool contains(const CircleResult& c, const Vec2& p, double eps = 1e-9) {
    return dist(c.center, p) <= c.radius + eps;
}

inline CircleResult circle_from_point(const Vec2& p) {
    return CircleResult{p, 0.0, {p}};
}

inline CircleResult circle_from_two(const Vec2& a, const Vec2& b) {
    const Vec2 center{(a.x + b.x) * 0.5, (a.y + b.y) * 0.5};
    return CircleResult{center, dist(center, a), {a, b}};
}

inline std::optional<CircleResult> circle_from_three(const Vec2& a,
                                                     const Vec2& b,
                                                     const Vec2& c) {
    const double d = 2.0 * (a.x * (b.y - c.y) + b.x * (c.y - a.y) + c.x * (a.y - b.y));
    if (std::abs(d) < 1e-12) return std::nullopt;
    const double a2 = dot(a, a);
    const double b2 = dot(b, b);
    const double c2 = dot(c, c);
    const Vec2 center{
        (a2 * (b.y - c.y) + b2 * (c.y - a.y) + c2 * (a.y - b.y)) / d,
        (a2 * (c.x - b.x) + b2 * (a.x - c.x) + c2 * (b.x - a.x)) / d,
    };
    return CircleResult{center, dist(center, a), {a, b, c}};
}

inline CircleResult circle_from_three_or_two(const Vec2& a,
                                             const Vec2& b,
                                             const Vec2& c) {
    if (auto circ = circle_from_three(a, b, c)) {
        return *circ;
    }
    // Collinear fallback: use the farthest pair among the three, which also
    // contains the middle point.
    const double ab = dist(a, b);
    const double bc = dist(b, c);
    const double ca = dist(c, a);
    if (ab >= bc && ab >= ca) return circle_from_two(a, b);
    if (bc >= ab && bc >= ca) return circle_from_two(b, c);
    return circle_from_two(c, a);
}

inline std::vector<Vec2> unique_points(std::vector<Vec2> pts) {
    std::sort(pts.begin(), pts.end(), [](const Vec2& u, const Vec2& v) {
        if (u.x != v.x) return u.x < v.x;
        return u.y < v.y;
    });
    pts.erase(std::unique(pts.begin(), pts.end(), [](const Vec2& u, const Vec2& v) {
        return u.x == v.x && u.y == v.y;
    }), pts.end());
    return pts;
}

inline CircleResult welzl_min_enclosing_circle(const std::vector<Vec2>& input,
                                               unsigned seed = 20260913u) {
    std::vector<Vec2> pts = unique_points(input);
    if (pts.empty()) return CircleResult{{0.0, 0.0}, 0.0, {}};

    std::mt19937 rng(seed);
    std::shuffle(pts.begin(), pts.end(), rng);

    bool has = false;
    CircleResult c;
    for (std::size_t i = 0; i < pts.size(); ++i) {
        const Vec2 p = pts[i];
        if (has && contains(c, p)) continue;
        c = circle_from_point(p);
        has = true;
        for (std::size_t j = 0; j < i; ++j) {
            const Vec2 q = pts[j];
            if (contains(c, q)) continue;
            c = circle_from_two(p, q);
            for (std::size_t k = 0; k < j; ++k) {
                const Vec2 r = pts[k];
                if (contains(c, r)) continue;
                c = circle_from_three_or_two(p, q, r);
            }
        }
    }
    return c;
}

inline CircleResult brute_force_min_enclosing_circle(const std::vector<Vec2>& input) {
    std::vector<Vec2> pts = unique_points(input);
    if (pts.empty()) return CircleResult{{0.0, 0.0}, 0.0, {}};
    if (pts.size() == 1) return circle_from_point(pts[0]);

    auto all_inside = [&](const CircleResult& c) {
        for (const Vec2& p : pts) {
            if (!contains(c, p, 1e-8)) return false;
        }
        return true;
    };

    CircleResult best = circle_from_point(pts[0]);
    double best_radius = std::numeric_limits<double>::infinity();

    for (std::size_t i = 0; i < pts.size(); ++i) {
        for (std::size_t j = i + 1; j < pts.size(); ++j) {
            CircleResult c = circle_from_two(pts[i], pts[j]);
            if (all_inside(c) && c.radius < best_radius) {
                best = c;
                best_radius = c.radius;
            }
        }
    }
    for (std::size_t i = 0; i < pts.size(); ++i) {
        for (std::size_t j = i + 1; j < pts.size(); ++j) {
            for (std::size_t k = j + 1; k < pts.size(); ++k) {
                auto opt = circle_from_three(pts[i], pts[j], pts[k]);
                if (!opt) continue;
                if (all_inside(*opt) && opt->radius < best_radius) {
                    best = *opt;
                    best_radius = opt->radius;
                }
            }
        }
    }
    if (!std::isfinite(best_radius)) {
        // Degenerate collinear case: the farthest pair gives the diameter disk.
        DiameterWitness d{{0.0, 0.0}, {0.0, 0.0}, -1.0};
        for (std::size_t i = 0; i < pts.size(); ++i) {
            for (std::size_t j = i + 1; j < pts.size(); ++j) {
                const double dd = dist(pts[i], pts[j]);
                if (dd > d.distance) d = {pts[i], pts[j], dd};
            }
        }
        return circle_from_two(d.a, d.b);
    }
    return best;
}

}  // namespace geom
