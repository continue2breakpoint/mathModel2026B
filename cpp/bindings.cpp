#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include "geom.hpp"

namespace py = pybind11;

PYBIND11_MODULE(geom_cpp, m) {
    m.doc() = "C++ geometry kernels for Q1: wedge intersection, rotating calipers and Welzl.";

    py::class_<geom::Vec2>(m, "Vec2")
        .def(py::init<double, double>(), py::arg("x"), py::arg("y"))
        .def_readwrite("x", &geom::Vec2::x)
        .def_readwrite("y", &geom::Vec2::y)
        .def("__repr__", [](const geom::Vec2& p) {
            return "Vec2(" + std::to_string(p.x) + ", " + std::to_string(p.y) + ")";
        });

    py::class_<geom::Observation>(m, "Observation")
        .def(py::init<geom::Vec2, double, double, double>(),
             py::arg("point"),
             py::arg("bearing_deg"),
             py::arg("half_width_deg") = 1.0,
             py::arg("max_range_m") = 1500.0)
        .def_readwrite("point", &geom::Observation::point)
        .def_readwrite("bearing_deg", &geom::Observation::bearing_deg)
        .def_readwrite("half_width_deg", &geom::Observation::half_width_deg)
        .def_readwrite("max_range_m", &geom::Observation::max_range_m);

    py::class_<geom::ConvexRegion>(m, "ConvexRegion")
        .def_readwrite("vertices", &geom::ConvexRegion::vertices)
        .def_readwrite("bounded", &geom::ConvexRegion::bounded)
        .def_readwrite("empty", &geom::ConvexRegion::empty)
        .def_readwrite("version", &geom::ConvexRegion::version)
        .def("is_empty", &geom::ConvexRegion::is_empty);

    py::class_<geom::DiameterWitness>(m, "DiameterWitness")
        .def_readwrite("a", &geom::DiameterWitness::a)
        .def_readwrite("b", &geom::DiameterWitness::b)
        .def_readwrite("distance", &geom::DiameterWitness::distance);

    py::class_<geom::CircleResult>(m, "CircleResult")
        .def_readwrite("center", &geom::CircleResult::center)
        .def_readwrite("radius", &geom::CircleResult::radius)
        .def_readwrite("basis", &geom::CircleResult::basis)
        .def("basis_size", &geom::CircleResult::basis_size);

    m.def("bearing_deg", &geom::bearing_deg,
          py::arg("from"), py::arg("to"));
    m.def("angle_diff_deg", &geom::angle_diff_deg,
          py::arg("a"), py::arg("b"));

    m.def("build_region",
          &geom::build_region,
          py::arg("observations"),
          py::arg("bound_radius") = 1800.0,
          py::arg("bound_sides") = 96,
          "Intersect all +/-half_width wedges with a circumscribed bound circle.");

    m.def("point_in_region", &geom::point_in_region,
          py::arg("point"), py::arg("region"), py::arg("eps") = 1e-8);

    m.def("brute_force_diameter", &geom::brute_force_diameter, py::arg("region"));
    m.def("rotating_calipers_diameter", &geom::rotating_calipers_diameter,
          py::arg("region"),
          "O(m) diameter using antipodal pairs / maximum parallel support-line spacing.");
    m.def("diameter_circle_covers", &geom::diameter_circle_covers,
          py::arg("region"), py::arg("diameter"), py::arg("eps") = 1e-9);

    m.def("welzl_min_enclosing_circle",
          [](const geom::ConvexRegion& region, unsigned seed) {
              return geom::welzl_min_enclosing_circle(region.vertices, seed);
          },
          py::arg("region"), py::arg("seed") = 20260913u,
          "Welzl randomized incremental minimum enclosing circle.");
    m.def("welzl_min_enclosing_circle_points",
          &geom::welzl_min_enclosing_circle,
          py::arg("points"), py::arg("seed") = 20260913u);
    m.def("brute_force_min_enclosing_circle",
          [](const geom::ConvexRegion& region) {
              return geom::brute_force_min_enclosing_circle(region.vertices);
          },
          py::arg("region"));
    m.def("brute_force_min_enclosing_circle_points",
          &geom::brute_force_min_enclosing_circle,
          py::arg("points"));

    m.def("can_clear", &geom::can_clear,
          py::arg("circle"), py::arg("tolerance_m") = 20.0);
}
