"""Hand-calculated B-01 display geometry fixtures; no camera or web runtime."""

from copy import deepcopy
import json
import unittest

import numpy as np

from pthoven_vision.geometry import (
    build_geometry,
    display_to_source,
    source_to_display,
)


def payload(**overrides):
    definition = {
        "source_size": {"width": 1600, "height": 900},
        "display_size": {"width": 800, "height": 600},
        "keyboard_rect": {"left": 80, "top": 300, "width": 640, "height": 150},
    }
    definition.update(overrides)
    return definition


class GeometryTests(unittest.TestCase):
    def assert_point(self, actual, expected):
        self.assertIsInstance(actual, list)
        np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-9)

    def test_identity_aspect_ratio_corners_center_and_inverse(self):
        geometry = build_geometry(payload(
            source_size={"width": 800, "height": 600},
        ))
        for source, display in [
            ([0, 0], [0, 0]), ([1, 0], [800, 0]),
            ([0, 1], [0, 600]), ([1, 1], [800, 600]),
            ([0.5, 0.5], [400, 300]), ([0.25, 0.75], [200, 450]),
        ]:
            with self.subTest(source=source):
                self.assert_point(source_to_display(geometry, source), display)
                self.assert_point(display_to_source(geometry, display), source)

    def test_contain_all_four_flips_with_hand_calculated_points(self):
        # 1600x900 -> 800x600: s=1/2, content=800x450, offset=(0,75).
        expected = {
            (False, False): ([200, 255], [0, 75], [800, 525]),
            (True, False): ([600, 255], [800, 75], [0, 525]),
            (False, True): ([200, 345], [0, 525], [800, 75]),
            (True, True): ([600, 345], [800, 525], [0, 75]),
        }
        for (flip_x, flip_y), displays in expected.items():
            geometry = build_geometry(payload(flip_x=flip_x, flip_y=flip_y))
            for source, display in zip(([0.25, 0.4], [0, 0], [1, 1]), displays):
                with self.subTest(flips=(flip_x, flip_y), source=source):
                    self.assert_point(source_to_display(geometry, source), display)
                    self.assert_point(display_to_source(geometry, display), source)
            self.assert_point(source_to_display(geometry, [0.5, 0.5]), [400, 300])

    def test_cover_all_four_flips_with_hand_calculated_points(self):
        # s=2/3, content=1066 2/3 x600, offset=(-133 1/3,0).
        expected = {
            (False, False): ([400 / 3, 240], [-400 / 3, 0], [2800 / 3, 600]),
            (True, False): ([2000 / 3, 240], [2800 / 3, 0], [-400 / 3, 600]),
            (False, True): ([400 / 3, 360], [-400 / 3, 600], [2800 / 3, 0]),
            (True, True): ([2000 / 3, 360], [2800 / 3, 600], [-400 / 3, 0]),
        }
        for (flip_x, flip_y), displays in expected.items():
            geometry = build_geometry(payload(object_fit="cover", flip_x=flip_x,
                                              flip_y=flip_y))
            for source, display in zip(([0.25, 0.4], [0, 0], [1, 1]), displays):
                with self.subTest(flips=(flip_x, flip_y), source=source):
                    self.assert_point(source_to_display(geometry, source), display)
                    self.assert_point(display_to_source(geometry, display), source)
            self.assert_point(source_to_display(geometry, [0.5, 0.5]), [400, 300])

    def test_portrait_content_has_horizontal_letterbox_or_vertical_crop(self):
        # contain: s=3/8, content=337.5x600, offset=(231.25,0).
        # cover: s=8/9, content=800x(12800/9), offset=(0,-3700/9).
        for object_fit, expected, inverse in [
            ("contain", [315.625, 240], [-7 / 18, 0.4]),
            ("cover", [200, 1420 / 9], [0.125, 293 / 640]),
        ]:
            with self.subTest(object_fit=object_fit):
                geometry = build_geometry(payload(
                    source_size={"width": 900, "height": 1600},
                    keyboard_rect={"left": 250, "top": 300, "width": 300,
                                   "height": 150},
                    object_fit=object_fit,
                ))
                self.assert_point(source_to_display(geometry, [0.25, 0.4]), expected)
                self.assert_point(display_to_source(geometry, [100, 240]), inverse)

    def test_resize_all_fits_and_flips_rebuilds_matrices_and_keys(self):
        # Halving 800x600 to 400x300 halves CSS points, rectangles and margins.
        expected = {
            ("contain", False, False): [100, 127.5],
            ("contain", True, False): [300, 127.5],
            ("contain", False, True): [100, 172.5],
            ("contain", True, True): [300, 172.5],
            ("cover", False, False): [200 / 3, 120],
            ("cover", True, False): [1000 / 3, 120],
            ("cover", False, True): [200 / 3, 180],
            ("cover", True, True): [1000 / 3, 180],
        }
        for (object_fit, flip_x, flip_y), display in expected.items():
            with self.subTest(fit=object_fit, flips=(flip_x, flip_y)):
                original = build_geometry(payload(object_fit=object_fit,
                                                  flip_x=flip_x, flip_y=flip_y))
                original_snapshot = deepcopy(original)
                resized = build_geometry(payload(
                    object_fit=object_fit, flip_x=flip_x, flip_y=flip_y,
                    display_size={"width": 400, "height": 300},
                    keyboard_rect={"left": 40, "top": 150, "width": 320,
                                   "height": 75},
                ), version=2)
                self.assert_point(source_to_display(resized, [0.25, 0.4]), display)
                self.assert_point(display_to_source(resized, display), [0.25, 0.4])
                self.assertEqual(resized["geometry_version"], 2)
                self.assertEqual(resized["key_regions"][0]["polygon_css"],
                                 [[40, 150], [80, 150], [80, 225], [40, 225]])
                self.assertEqual(resized["key_regions"][0]["boundary_margin_css"], 2)
                self.assertEqual(original, original_snapshot)

    def test_all_four_matrices_have_known_values_and_inverse_composition(self):
        geometry = build_geometry(payload())
        np.testing.assert_allclose(geometry["source_to_display_matrix"],
                                   [[800, 0, 0], [0, 450, 75], [0, 0, 1]])
        np.testing.assert_allclose(geometry["display_to_source_matrix"],
                                   [[1 / 800, 0, 0], [0, 1 / 450, -1 / 6],
                                    [0, 0, 1]])
        np.testing.assert_allclose(geometry["display_to_key_matrix"],
                                   [[1, 0, -80], [0, 1, -300], [0, 0, 1]])
        for a, b in [("source_to_display_matrix", "display_to_source_matrix"),
                     ("display_to_key_matrix", "key_to_display_matrix")]:
            np.testing.assert_allclose(np.array(geometry[a]) @ geometry[b],
                                       np.eye(3), rtol=0, atol=1e-12)
            np.testing.assert_allclose(np.array(geometry[b]) @ geometry[a],
                                       np.eye(3), rtol=0, atol=1e-12)
        # (u,v)=(1/4,3/5) -> display=(200,345) -> keyboard local=(120,45).
        key_point = (np.array(geometry["display_to_key_matrix"])
                     @ geometry["source_to_display_matrix"] @ [0.25, 0.6, 1])
        np.testing.assert_allclose(key_point, [120, 45, 1], rtol=0, atol=1e-9)

    def test_white_keys_midi_order_polygons_margins_and_flags(self):
        geometry = build_geometry(payload())
        keys = geometry["key_regions"]
        self.assertEqual([key["key_id"] for key in keys],
                         ["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"])
        self.assertEqual([key["midi_note"] for key in keys],
                         [60, 62, 64, 65, 67, 69, 71, 72])
        for i, key in enumerate(keys):
            self.assertIs(key["enabled"], True)
            self.assertEqual(key["layer"], 0)
            self.assertEqual(key["boundary_margin_css"], 4)
            self.assertEqual(key["polygon_css"],
                             [[80 + 80 * i, 300], [160 + 80 * i, 300],
                              [160 + 80 * i, 450], [80 + 80 * i, 450]])

    def test_flips_change_image_transform_not_css_key_layout(self):
        reference = build_geometry(payload())["key_regions"]
        for flip_x, flip_y in [(True, False), (False, True), (True, True)]:
            geometry = build_geometry(payload(flip_x=flip_x, flip_y=flip_y))
            self.assertEqual(geometry["key_regions"], reference)

    def test_dpr_is_metadata_only_for_both_fit_modes(self):
        for fit in ("contain", "cover"):
            base = build_geometry(payload(object_fit=fit))
            for dpr in (0.75, 2, 3):
                with self.subTest(fit=fit, dpr=dpr):
                    geometry = build_geometry(payload(object_fit=fit,
                                                      device_pixel_ratio=dpr))
                    self.assertEqual(geometry.pop("device_pixel_ratio"), dpr)
                    reference = deepcopy(base)
                    reference.pop("device_pixel_ratio")
                    self.assertEqual(geometry, reference)

    def test_keyboard_can_touch_content_edges_for_contain_and_cover(self):
        for fit, rect in [
            ("contain", {"left": 0, "top": 75, "width": 800, "height": 450}),
            ("cover", {"left": 0, "top": 0, "width": 800, "height": 600}),
        ]:
            with self.subTest(fit=fit):
                self.assertEqual(build_geometry(payload(object_fit=fit,
                                                       keyboard_rect=rect))
                                 ["keyboard_rect"], rect)

    def test_source_and_display_outside_bounds_are_not_clamped(self):
        geometry = build_geometry(payload())
        self.assert_point(source_to_display(geometry, [-0.25, 1.25]), [-200, 637.5])
        self.assert_point(display_to_source(geometry, [400, 0]), [0.5, -1 / 6])
        self.assert_point(display_to_source(geometry, [400, 600]), [0.5, 7 / 6])
        cover = build_geometry(payload(object_fit="cover"))
        self.assert_point(display_to_source(cover, [0, 300]), [0.125, 0.5])
        self.assert_point(display_to_source(cover, [800, 300]), [0.875, 0.5])

    def test_export_is_json_safe_independent_and_accepts_its_own_matrices(self):
        definition = payload(source_size={"width": np.int64(1600), "height": 900})
        before = deepcopy(definition)
        geometry = build_geometry(definition, version=np.int64(3))
        self.assertEqual(definition, before)
        self.assertEqual(json.loads(json.dumps(geometry, allow_nan=False)), geometry)
        self.assertEqual(build_geometry(geometry, version=3), geometry)
        geometry["source_size"]["width"] = 1
        geometry["keyboard_rect"]["left"] = 0
        self.assertEqual(definition, before)


class InvalidGeometryTests(unittest.TestCase):
    def test_missing_fields_and_invalid_shapes_raise_value_error(self):
        for value in (None, [], "geometry", 1):
            with self.subTest(payload=value), self.assertRaises(ValueError):
                build_geometry(value)
        for field in ("source_size", "display_size", "keyboard_rect"):
            definition = payload()
            del definition[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                build_geometry(definition)
            for bad in (None, [], [800, 600], "800x600"):
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    build_geometry(payload(**{field: bad}))
            for name in tuple(payload()[field]):
                definition = payload()
                del definition[field][name]
                with self.subTest(field=field, missing=name), self.assertRaises(ValueError):
                    build_geometry(definition)

    def test_numeric_fields_are_strict_and_finite(self):
        bad_values = [True, False, np.bool_(True), "800", None, [], 1 + 0j,
                      float("nan"), float("inf"), -float("inf"), 10 ** 400]
        for field in ("source_size", "display_size", "keyboard_rect"):
            for name in tuple(payload()[field]):
                for bad in bad_values:
                    definition = payload()
                    definition[field][name] = bad
                    with self.subTest(field=field, name=name, bad=bad):
                        with self.assertRaises(ValueError):
                            build_geometry(definition)
        for bad in bad_values + [0, -1]:
            with self.subTest(dpr=bad), self.assertRaises(ValueError):
                build_geometry(payload(device_pixel_ratio=bad))

    def test_dimensions_must_be_positive(self):
        for field in ("source_size", "display_size", "keyboard_rect"):
            for name in ("width", "height"):
                for bad in (0, -1):
                    definition = payload()
                    definition[field][name] = bad
                    with self.subTest(field=field, name=name, bad=bad):
                        with self.assertRaises(ValueError):
                            build_geometry(definition)

    def test_flags_object_fit_and_version_are_strict(self):
        for field in ("flip_x", "flip_y"):
            for bad in (0, 1, "false", None, np.bool_(True)):
                with self.subTest(field=field, bad=bad), self.assertRaises(ValueError):
                    build_geometry(payload(**{field: bad}))
        for bad in (None, True, "fill", "CONTAIN", [], {}):
            with self.subTest(fit=bad), self.assertRaises(ValueError):
                build_geometry(payload(object_fit=bad))
        for bad in (True, np.bool_(True), 0, -1, 1.0, "1", None,
                    float("nan"), float("inf")):
            with self.subTest(version=bad), self.assertRaises(ValueError):
                build_geometry(payload(), version=bad)

    def test_keyboard_must_be_entirely_inside_visible_content(self):
        for fit, rect in [
            ("contain", {"left": 80, "top": 0, "width": 640, "height": 30}),
            ("contain", {"left": 80, "top": 74, "width": 640, "height": 100}),
            ("contain", {"left": 80, "top": 500, "width": 640, "height": 26}),
            ("contain", {"left": -1, "top": 300, "width": 100, "height": 100}),
            ("cover", {"left": -50, "top": 300, "width": 100, "height": 100}),
            ("cover", {"left": 750, "top": 300, "width": 100, "height": 100}),
            ("cover", {"left": 80, "top": 590, "width": 640, "height": 11}),
            ("cover", {"left": 80, "top": -1, "width": 640, "height": 100}),
        ]:
            with self.subTest(fit=fit, rect=rect), self.assertRaisesRegex(
                    ValueError, "visible video content"):
                build_geometry(payload(object_fit=fit, keyboard_rect=rect))
        # Portrait contain has left/right letterbox; cover has cropped top/bottom.
        for fit, rect in [
            ("contain", {"left": 0, "top": 300, "width": 100, "height": 100}),
            ("contain", {"left": 500, "top": 300, "width": 100, "height": 100}),
            ("cover", {"left": 250, "top": -10, "width": 300, "height": 100}),
        ]:
            with self.subTest(portrait_fit=fit, rect=rect), self.assertRaises(ValueError):
                build_geometry(payload(source_size={"width": 900, "height": 1600},
                                       object_fit=fit, keyboard_rect=rect))

    def test_supplied_matrices_cannot_contain_nonfinite_or_coerced_values(self):
        reference = build_geometry(payload())
        for name in ("source_to_display_matrix", "display_to_source_matrix",
                     "display_to_key_matrix", "key_to_display_matrix"):
            for bad in (float("nan"), float("inf"), -float("inf"), True, "1"):
                matrix = deepcopy(reference[name])
                matrix[0][0] = bad
                with self.subTest(matrix=name, bad=bad), self.assertRaises(ValueError):
                    build_geometry(payload(**{name: matrix}))
            for bad in ([[1, 0], [0, 1]], [[1, 0, 0], [0]], None):
                with self.subTest(matrix=name, shape=bad), self.assertRaises(ValueError):
                    build_geometry(payload(**{name: bad}))
            matrix = deepcopy(reference[name])
            matrix[0][2] += 1
            with self.subTest(inconsistent=name), self.assertRaises(ValueError):
                build_geometry(payload(**{name: matrix}))

    def test_helpers_reject_nonfinite_singular_and_inconsistent_matrices(self):
        for helper in (source_to_display, display_to_source):
            for name in ("source_to_display_matrix", "display_to_source_matrix"):
                for bad in (float("nan"), float("inf"), -float("inf"), True, "1"):
                    geometry = build_geometry(payload())
                    geometry[name][0][0] = bad
                    with self.subTest(helper=helper.__name__, matrix=name, bad=bad):
                        with self.assertRaises(ValueError):
                            helper(geometry, [0.5, 0.5])
            for bad in (
                [[0, 0, 0], [0, 1, 0], [0, 0, 1]],
                [[1, 0, 0], [0, 1, 0], [0.1, 0, 1]],
                [[1, 0], [0, 1]],
                [[1, 0, 0], [0]],
                None,
            ):
                geometry = build_geometry(payload())
                geometry["source_to_display_matrix"] = bad
                with self.subTest(helper=helper.__name__, bad=bad):
                    with self.assertRaises(ValueError):
                        helper(geometry, [0, 0])
            geometry = build_geometry(payload())
            geometry["display_to_source_matrix"][0][2] = 1
            with self.assertRaises(ValueError):
                helper(geometry, [0, 0])
            for geometry in (None, {}, []):
                with self.subTest(geometry=geometry), self.assertRaises(ValueError):
                    helper(geometry, [0, 0])

    def test_tiny_inverse_scale_cannot_be_replaced_by_zero(self):
        geometry = build_geometry(payload(
            display_size={"width": 1e16, "height": 1e16},
            keyboard_rect={"left": 1e15, "top": 3e15, "width": 8e15,
                           "height": 1e15},
        ))
        # x_display=1e16*u, hence u=1e-16*x_display, not a constant zero.
        self.assertEqual(geometry["display_to_source_matrix"][0][0], 1e-16)
        np.testing.assert_allclose(display_to_source(geometry, [5e15, 5e15]),
                                   [0.5, 0.5], rtol=0, atol=1e-12)
        geometry["display_to_source_matrix"][0][0] = 0.0
        with self.assertRaises(ValueError):
            build_geometry(geometry)
        for helper in (source_to_display, display_to_source):
            with self.subTest(helper=helper.__name__), self.assertRaises(ValueError):
                helper(geometry, [0.5, 0.5])

    def test_points_must_be_two_strict_finite_numbers(self):
        geometry = build_geometry(payload())
        for helper in (source_to_display, display_to_source):
            for bad in ([True, 0], [np.bool_(False), 0], ["0", 0], [None, 0],
                        [float("nan"), 0], [0, float("inf")], [0, -float("inf")],
                        [1 + 0j, 0], [], [0], [0, 0, 1], [[0, 0]], None):
                with self.subTest(helper=helper.__name__, xy=bad):
                    with self.assertRaises(ValueError):
                        helper(geometry, bad)
        self.assertEqual(source_to_display(geometry, np.array([0.5, 0.5])), [400, 300])
        self.assertEqual(source_to_display(geometry, (0.5, 0.5)), [400, 300])

    def test_overflow_underflow_and_unrepresentable_geometry_are_rejected(self):
        for definition in [
            payload(source_size={"width": 1e-308, "height": 1e-308},
                    display_size={"width": 1e308, "height": 1e308}),
            payload(source_size={"width": 1e308, "height": 1e308},
                    display_size={"width": 1e-308, "height": 1e-308}),
            payload(keyboard_rect={"left": 1e308, "top": 300, "width": 1e308,
                                   "height": 100}),
            payload(display_size={"width": 1e20, "height": 1e20},
                    keyboard_rect={"left": 1e18, "top": 4e19, "width": 1,
                                   "height": 100}),
            payload(display_size={"width": 1e20, "height": 1e20},
                    keyboard_rect={"left": 1e18, "top": 4e19, "width": 256,
                                   "height": 10000}),
        ]:
            with self.subTest(definition=definition), self.assertRaises(ValueError):
                build_geometry(definition)
        with self.assertRaisesRegex(ValueError, "transformed xy must be finite"):
            source_to_display(build_geometry(payload()), [1e308, 0.5])


if __name__ == "__main__":
    unittest.main()
