"""Pure NumPy display geometry for the B-01 virtual white keyboard.

Source points are normalized, unflipped ``[u, v]`` coordinates; display and
keyboard coordinates use CSS pixels, never device pixels. Object position is
always centered. These functions preserve off-screen coordinates for diagnosis;
the caller decides whether a point is eligible for note mapping.
"""

from numbers import Integral, Real

import numpy as np

__all__ = ["build_geometry", "source_to_display", "display_to_source"]

_WHITE_KEYS = (
    ("C4", 60), ("D4", 62), ("E4", 64), ("F4", 65),
    ("G4", 67), ("A4", 69), ("B4", 71), ("C5", 72),
)


def _mapping(value, name):
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a dict")
    return value


def _required(value, key, name):
    if key not in value:
        raise ValueError(f"{name}.{key} is required")
    return value[key]


def _number(value, name, *, positive=False):
    # Checking before conversion prevents bools and numeric strings being coerced.
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number (not bool)")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite float64 number") from exc
    if not np.isfinite(result) or (positive and result <= 0):
        qualifier = "positive finite" if positive else "finite"
        raise ValueError(f"{name} must be a {qualifier} number")
    return result


def _size(value, name):
    value = _mapping(value, name)
    return {
        key: _number(_required(value, key, name), f"{name}.{key}", positive=True)
        for key in ("width", "height")
    }


def _array(value, shape, name):
    try:
        # Object dtype preserves mixed bool/string inputs for strict validation.
        items = np.asarray(value, dtype=object)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must have shape {shape}") from exc
    if items.shape != shape:
        raise ValueError(f"{name} must have shape {shape}")
    values = [_number(item, name) for item in items.flat]
    return np.asarray(values, dtype=np.float64).reshape(shape)


def _matrix(value, name):
    matrix = _array(value, (3, 3), name)
    if not np.array_equal(matrix[2], [0.0, 0.0, 1.0]):
        raise ValueError(f"{name} must be a 2D affine matrix")
    return matrix


def _inverse(matrix, name):
    try:
        with np.errstate(all="ignore"):
            inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError as exc:
        raise ValueError(f"{name} must be invertible") from exc
    if not np.isfinite(inverse).all():
        raise ValueError(f"{name} inverse must be finite")
    return inverse


def _matching_matrix(value, expected, name):
    matrix = _matrix(value, name)
    # An absolute tolerance could accept zero in place of a tiny inverse scale.
    with np.errstate(all="ignore"):
        matches = np.allclose(matrix, expected, rtol=1e-12, atol=0.0)
    if not matches:
        raise ValueError(f"{name} does not match the geometry")
    return matrix


def build_geometry(payload: dict, version: int = 1) -> dict:
    """Validate a display definition and return independent JSON-safe geometry.

    Required payload fields are ``source_size`` and ``display_size`` (dicts with
    positive ``width``/``height``), and ``keyboard_rect`` (``left``, ``top``,
    positive ``width``/``height`` in CSS px). Optional fields are ``object_fit``
    (default ``"contain"``), strict bool ``flip_x``/``flip_y`` (default false),
    and positive ``device_pixel_ratio`` (default 1, metadata only).

    The entire keyboard must fit inside the intersection of the displayed video
    content and its container. Eight enabled equal-width white keys C4 through
    C5 use layer 0 and a boundary margin of 5% of a key's width. All transforms
    are 3x3 matrices acting on homogeneous column vectors. Supplied transform
    matrices, if present, must agree with the computed transforms. ``version``
    is a caller-owned positive integer; this module does not increment it.
    Invalid input or unrepresentable float64 geometry raises ``ValueError``.
    """
    payload = _mapping(payload, "payload")
    if isinstance(version, (bool, np.bool_)) or not isinstance(version, Integral):
        raise ValueError("version must be a positive integer")
    if version < 1:
        raise ValueError("version must be a positive integer")

    source = _size(_required(payload, "source_size", "payload"), "source_size")
    display = _size(_required(payload, "display_size", "payload"), "display_size")
    object_fit = payload.get("object_fit", "contain")
    if not isinstance(object_fit, str) or object_fit not in ("contain", "cover"):
        raise ValueError("object_fit must be 'contain' or 'cover'")
    flips = {}
    for name in ("flip_x", "flip_y"):
        flips[name] = payload.get(name, False)
        if type(flips[name]) is not bool:
            raise ValueError(f"{name} must be a bool")
    dpr = _number(payload.get("device_pixel_ratio", 1),
                  "device_pixel_ratio", positive=True)

    rect_input = _mapping(_required(payload, "keyboard_rect", "payload"),
                          "keyboard_rect")
    rect = {
        name: _number(_required(rect_input, name, "keyboard_rect"),
                      f"keyboard_rect.{name}", positive=name in ("width", "height"))
        for name in ("left", "top", "width", "height")
    }
    right = _number(rect["left"] + rect["width"], "keyboard_rect.right")
    bottom = _number(rect["top"] + rect["height"], "keyboard_rect.bottom")
    if right <= rect["left"] or bottom <= rect["top"]:
        raise ValueError("keyboard_rect must have representable positive area")

    sw, sh = source["width"], source["height"]
    dw, dh = display["width"], display["height"]
    fit = min if object_fit == "contain" else max
    scale = _number(fit(dw / sw, dh / sh), "display scale", positive=True)
    rw = _number(scale * sw, "rendered width", positive=True)
    rh = _number(scale * sh, "rendered height", positive=True)
    ox, oy = (dw - rw) / 2.0, (dh - rh) / 2.0
    content_left, content_top = max(0.0, ox), max(0.0, oy)
    content_right, content_bottom = min(dw, ox + rw), min(dh, oy + rh)
    if (rect["left"] < content_left or rect["top"] < content_top
            or right > content_right or bottom > content_bottom):
        raise ValueError("keyboard_rect must lie within the visible video content "
                         "intersection, excluding letterbox areas")

    forward = _matrix([
        [-rw if flips["flip_x"] else rw, 0.0, ox + (rw if flips["flip_x"] else 0.0)],
        [0.0, -rh if flips["flip_y"] else rh, oy + (rh if flips["flip_y"] else 0.0)],
        [0.0, 0.0, 1.0],
    ], "source_to_display_matrix")
    inverse = _inverse(forward, "source_to_display_matrix")
    display_to_key = np.array([
        [1.0, 0.0, -rect["left"]],
        [0.0, 1.0, -rect["top"]],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    matrices = {
        "source_to_display_matrix": forward,
        "display_to_source_matrix": inverse,
        "display_to_key_matrix": display_to_key,
        "key_to_display_matrix": _inverse(display_to_key, "display_to_key_matrix"),
    }
    for name, matrix in matrices.items():
        if name in payload:
            _matching_matrix(payload[name], matrix, name)

    key_width = rect["width"] / len(_WHITE_KEYS)
    margin = _number(key_width * 0.05, "boundary_margin_css", positive=True)
    edges = [rect["left"] + rect["width"] * (i / 8) for i in range(9)]
    if any(edges[i + 1] <= edges[i] for i in range(8)):
        raise ValueError("keyboard_rect must have eight representable key widths")
    keys = [
        {
            "key_id": key_id,
            "midi_note": midi_note,
            "polygon_css": [[edges[i], rect["top"]], [edges[i + 1], rect["top"]],
                            [edges[i + 1], bottom], [edges[i], bottom]],
            "layer": 0,
            "enabled": True,
            "boundary_margin_css": margin,
        }
        for i, (key_id, midi_note) in enumerate(_WHITE_KEYS)
    ]
    return {
        "geometry_version": int(version),
        "source_size": source,
        "display_size": display,
        "object_fit": object_fit,
        **flips,
        "device_pixel_ratio": dpr,
        "keyboard_rect": rect,
        **{name: matrix.tolist() for name, matrix in matrices.items()},
        "key_regions": keys,
    }


def _source_matrices(geometry):
    geometry = _mapping(geometry, "geometry")
    name = "source_to_display_matrix"
    forward = _matrix(_required(geometry, name, "geometry"), name)
    inverse = _inverse(forward, name)
    if "display_to_source_matrix" in geometry:
        inverse = _matching_matrix(geometry["display_to_source_matrix"], inverse,
                                   "display_to_source_matrix")
    return forward, inverse


def _transform(matrix, xy):
    point = _array(xy, (2,), "xy")
    with np.errstate(all="ignore"):
        result = matrix @ np.array([point[0], point[1], 1.0], dtype=np.float64)
    if not np.isfinite(result).all():
        raise ValueError("transformed xy must be finite")
    return result[:2].tolist()


def source_to_display(geometry: dict, xy) -> list:
    """Map normalized source ``[u, v]`` to CSS ``[x, y]`` without clamping.

    ``geometry`` is a build_geometry result. Invalid points, nonfinite/singular
    matrices, or inconsistent forward/inverse matrices raise ``ValueError``.
    """
    forward, _ = _source_matrices(geometry)
    return _transform(forward, xy)


def display_to_source(geometry: dict, xy) -> list:
    """Map CSS ``[x, y]`` back to normalized source coordinates, without clamping.

    Letterbox and cropped/off-screen points retain their mathematical inverse
    coordinates; these helpers do not perform key hit-testing or eligibility.
    """
    _, inverse = _source_matrices(geometry)
    return _transform(inverse, xy)
