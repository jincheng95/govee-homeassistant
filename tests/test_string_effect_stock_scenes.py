"""Stock spatial scenes rebuilt through ``govee.apply_diy_effect`` (fork feature).

``SCENES`` holds every spatial stock scene of the H70CX family as its layers
store them. Each entry is the scene name, the two layered header bytes after
the layer count (None for a single effect without the layered wrapper), and per
layer the effect id, the parameter block and the palette as flat RGB bytes.
Passing the block as ``params`` must rebuild the stock record byte for byte.
"""

from __future__ import annotations

from typing import Any

import pytest
import voluptuous as vol
from homeassistant.exceptions import ServiceValidationError

from custom_components.govee.api.protocol import h70cx
from custom_components.govee.api.protocol.errors import FrameError
from custom_components.govee.api.protocol.packets import DIY_COMMIT_SUB_MODE, commit_frame
from custom_components.govee.api.protocol.scenes import H70CX_MARKER, terminated_chunks
from custom_components.govee.diy_actions import SERVICE_APPLY_DIY_EFFECT_SCHEMA
from tests.test_string_effect_service import COLORS, _call, _coordinator, _run

Block = tuple[int, bytes, bytes]
Scene = tuple[str, tuple[int, int] | None, tuple[Block, ...]]

# Each effect id under the generic name that carries it.
NAME = {
    1: "flow",
    2: "layered",
    3: "rainbow",
    4: "flame",
    5: "vortex",
    6: "diffuse",
    7: "concentric",
    8: "rotate",
    9: "spiral",
    10: "starlight",
    14: "wave",
    15: "flexing",
    16: "candy_crush",
    17: "blend",
    18: "spin_3",
}

MODE = {(2, 0): "default", (3, 0): "variant_3", (1, 2): "variant_1_2"}

SCENES: tuple[Scene, ...] = (
    ('Ocean B', (3, 0), (
        (14, bytes([1, 50, 2, 8, 1, 13, 2, 1]), bytes([0, 91, 255, 0, 175, 255])),
        (14, bytes([1, 43, 2, 12, 1, 16, 1, 0]), bytes([0, 0, 255])),
    )),
    ('Waterfall B', (2, 0), (
        (17, bytes([2, 50]), bytes([53, 255, 255, 0, 90, 255, 76, 191, 255, 0, 122, 255])),
    )),
    ('Rainbow B', None, (
        (3, bytes([4, 5, 92]), bytes([255, 0, 0, 255, 127, 0, 255, 255, 0, 0, 255, 0, 0, 0, 255, 0, 255, 255, 139, 0, 255])),
    )),
    ('Aurora B', None, (
        (1, bytes([3, 9, 98, 1, 1, 72]), bytes([8, 255, 170, 8, 255, 21, 0, 100, 255, 118, 0, 255, 159, 103, 255])),
    )),
    ('Sunrise B', None, (
        (5, bytes([3, 14, 90, 1, 4, 0, 0, 0, 0]), bytes([244, 162, 27, 249, 113, 34, 227, 90, 0, 255, 255, 0, 222, 52, 54, 222, 65, 66])),
    )),
    ('Sunset B', None, (
        (5, bytes([4, 14, 82, 1, 3, 0, 0, 0, 0]), bytes([248, 150, 66, 252, 79, 0, 139, 23, 118, 100, 13, 108, 45, 50, 252, 0, 0, 0, 0, 0, 0, 0, 0, 0])),
    )),
    ('Rustling leaves B', (2, 0), (
        (1, bytes([3, 5, 90, 1, 0, 0]), bytes([0, 255, 39, 7, 255, 92, 26, 255, 36, 77, 255, 0])),
        (10, bytes([30, 0, 1, 0, 0, 30]), bytes([0, 255, 0, 255, 255, 0])),
    )),
    ('Ripple B', (2, 0), (
        (6, bytes([5, 92, 0, 36, 5, 0, 0, 0, 0]), bytes([0, 255, 255, 0, 0, 0, 0, 255, 118, 0, 0, 0, 0, 98, 255, 0, 0, 0])),
        (6, bytes([7, 93, 0, 30, 5, 155, 0, 155, 0]), bytes([0, 0, 0, 0, 95, 255, 0, 0, 0, 0, 255, 135])),
    )),
    ('Flame B', None, (
        (4, bytes([10, 94, 1, 0]), bytes([255, 16, 0, 255, 70, 0, 255, 193, 0])),
    )),
    ('Beach B', None, (
        (4, bytes([1, 93, 1, 0]), bytes([0, 96, 255, 55, 255, 255])),
    )),
    ('Starry Night B', (2, 0), (
        (6, bytes([4, 80, 0, 1, 5, 0, 0, 0, 0]), bytes([0, 255, 159, 0, 255, 255, 0, 62, 255, 20, 0, 255, 0, 0, 255, 0, 87, 255, 20, 255, 255])),
        (10, bytes([50, 0, 1, 0, 0, 1]), bytes([255, 255, 0, 255, 229, 255])),
    )),
    ('Universe B', (2, 0), (
        (6, bytes([4, 93, 1, 1, 5, 0, 0, 0, 0]), bytes([0, 255, 255, 0, 0, 255, 193, 0, 255])),
        (9, bytes([5, 15, 93, 0, 1, 2, 0, 100, 0]), bytes([0, 0, 0, 255, 255, 255, 255, 0, 0])),
        (10, bytes([50, 0, 1, 0, 0, 1]), bytes([255, 255, 0, 255, 229, 255])),
    )),
    ('Cornfield B', (2, 0), (
        (9, bytes([9, 24, 75, 1, 0, 5, 5, 100, 0]), bytes([255, 69, 0, 255, 131, 0])),
        (9, bytes([9, 23, 75, 1, 0, 4, 5, 100, 0]), bytes([255, 155, 0, 255, 85, 7])),
    )),
    ('Firefly B', (2, 0), (
        (9, bytes([7, 24, 80, 1, 0, 5, 15, 100, 0]), bytes([0, 255, 45, 9, 255, 104])),
        (9, bytes([7, 15, 80, 1, 0, 4, 15, 100, 0]), bytes([0, 255, 0, 0, 255, 115])),
        (10, bytes([100, 0, 1, 0, 0, 3]), bytes([255, 255, 0, 255, 255, 104])),
    )),
    ('Spring Wind B', (2, 0), (
        (9, bytes([9, 24, 90, 1, 0, 5, 10, 100, 0]), bytes([255, 224, 172, 255, 219, 39])),
    )),
    ('Wave B', (2, 0), (
        (9, bytes([7, 24, 90, 1, 0, 4, 10, 30, 0]), bytes([0, 0, 255, 0, 204, 255])),
        (9, bytes([7, 15, 90, 1, 0, 4, 10, 30, 0]), bytes([0, 0, 255, 0, 210, 255])),
        (1, bytes([5, 9, 95, 0, 1, 70]), bytes([0, 255, 255, 0, 255, 127])),
    )),
    ('Maximalism', (3, 0), (
        (10, bytes([90, 0, 90, 0, 0, 5]), bytes([166, 14, 0, 31, 164, 111])),
        (1, bytes([5, 3, 50, 1, 0, 0]), bytes([255, 172, 58, 255, 174, 84, 255, 195, 124, 255, 157, 63])),
    )),
    ('Hot Pink B', (2, 0), (
        (2, bytes([10, 3, 90, 0, 1, 60, 1]), bytes([255, 139, 20, 255, 51, 167, 84, 255, 196, 255, 71, 153, 255, 81, 204, 138, 232, 255, 255, 154, 150])),
        (10, bytes([35, 0, 65, 0, 1, 65]), bytes([255, 216, 162, 255, 170, 20])),
        (1, bytes([8, 4, 90, 1, 1, 6]), bytes([255, 76, 208, 84, 255, 196, 255, 122, 166])),
    )),
    ('Christmas Grid B', (3, 0), (
        (2, bytes([5, 1, 90, 0, 0, 0, 1]), bytes([255, 0, 0, 255, 0, 0])),
        (2, bytes([5, 3, 90, 0, 0, 0, 1]), bytes([0, 255, 0, 0, 255, 0])),
    )),
    ('Ribbon B', (2, 0), (
        (9, bytes([7, 23, 80, 1, 0, 2, 50, 100, 0]), bytes([139, 0, 255, 0, 255, 255, 0, 255, 150, 255, 123, 0, 255, 65, 94])),
        (10, bytes([10, 0, 50, 0, 0, 79]), bytes([255, 204, 0, 255, 255, 255])),
    )),
    ('Christmas Bell B', None, (
        (2, bytes([4, 3, 98, 0, 1, 0, 1]), bytes([255, 0, 0, 255, 255, 255, 0, 255, 0, 255, 255, 255])),
    )),
    ('Christmas Stripe B', None, (
        (1, bytes([7, 1, 98, 0, 0, 0]), bytes([255, 0, 0, 0, 255, 0])),
    )),
    ('Candy Cane B', None, (
        (2, bytes([8, 5, 98, 0, 0, 0, 1]), bytes([255, 0, 0, 255, 255, 255])),
    )),
    ("Valentine's Day B", None, (
        (1, bytes([6, 9, 95, 1, 0, 0]), bytes([222, 56, 65, 248, 0, 15, 0, 0, 0, 255, 104, 134])),
    )),
    ('Ribbon B', None, (
        (1, bytes([8, 5, 97, 0, 0, 0]), bytes([255, 19, 0, 255, 255, 0, 0, 0, 0, 0, 0, 0])),
    )),
    ('Carnival B', None, (
        (8, bytes([4, 3, 93, 1, 17, 4, 0, 0, 0, 0]), bytes([255, 255, 0, 255, 127, 0, 255, 55, 0])),
    )),
    ('Romantic B', None, (
        (1, bytes([3, 5, 93, 1, 0, 0]), bytes([255, 33, 69, 255, 112, 255, 255, 255, 255])),
    )),
    ('Heartbeat B', (2, 0), (
        (9, bytes([7, 24, 93, 1, 0, 5, 15, 100, 0]), bytes([255, 29, 35, 255, 90, 143])),
        (9, bytes([7, 15, 93, 1, 0, 4, 15, 100, 0]), bytes([255, 29, 35, 255, 90, 143])),
        (10, bytes([50, 0, 1, 0, 0, 96]), bytes([255, 192, 255, 255, 229, 255])),
    )),
    ('Christmas Gift B', (2, 0), (
        (8, bytes([4, 3, 90, 0, 1, 4, 0, 0, 0, 0]), bytes([0, 255, 0, 255, 0, 0])),
        (10, bytes([50, 0, 1, 0, 0, 96]), bytes([255, 192, 255, 255, 229, 255])),
    )),
    ('Party B', (2, 0), (
        (1, bytes([6, 3, 98, 0, 1, 0]), bytes([255, 56, 185, 255, 24, 0, 76, 46, 255, 0, 142, 255, 0, 255, 61, 0, 255, 255, 255, 255, 0])),
        (10, bytes([100, 0, 1, 0, 0, 89]), bytes([255, 0, 0, 255, 127, 0, 0, 255, 0, 255, 255, 0, 0, 0, 255, 0, 255, 255, 139, 0, 255, 255, 255, 255])),
    )),
    ('New Years B', (2, 0), (
        (1, bytes([5, 9, 95, 1, 1, 0]), bytes([255, 127, 0, 255, 255, 0, 255, 0, 0])),
        (9, bytes([7, 15, 93, 1, 0, 4, 15, 100, 0]), bytes([255, 0, 0, 255, 127, 0])),
        (10, bytes([50, 0, 1, 0, 0, 96]), bytes([255, 255, 0, 255, 229, 255])),
    )),
    ('Thanksgiving B', (2, 0), (
        (1, bytes([6, 5, 95, 1, 0, 0]), bytes([0, 255, 0, 255, 47, 0, 255, 127, 0, 0, 0, 0])),
        (10, bytes([50, 0, 1, 0, 0, 50]), bytes([157, 255, 197, 255, 183, 236])),
    )),
    ('Easter B', (2, 0), (
        (2, bytes([2, 6, 94, 1, 1, 10, 1]), bytes([255, 0, 0, 255, 255, 0, 0, 255, 0, 0, 255, 202])),
        (2, bytes([2, 5, 94, 1, 1, 10, 1]), bytes([255, 0, 0, 255, 255, 0, 0, 255, 0, 0, 255, 207])),
    )),
    ('Birthday B', (2, 0), (
        (10, bytes([50, 0, 1, 0, 0, 46]), bytes([255, 127, 0, 255, 255, 0, 0, 255, 0, 0, 255, 255])),
        (1, bytes([6, 2, 97, 0, 0, 0]), bytes([255, 88, 0, 255, 50, 150, 0, 0, 0, 255, 74, 0, 255, 255, 0, 255, 75, 255, 0, 0, 0])),
    )),
    ('Christmas Night B', (2, 0), (
        (1, bytes([6, 9, 95, 1, 0, 0]), bytes([255, 0, 0, 0, 255, 0, 0, 0, 0])),
        (10, bytes([50, 0, 1, 0, 0, 50]), bytes([157, 255, 197, 255, 183, 236])),
    )),
    ('Fireworks B', (1, 2), (
        (7, bytes([3, 96, 1, 50, 5, 0, 0, 0, 0]), bytes([255, 81, 0, 255, 255, 0, 255, 127, 0])),
        (10, bytes([50, 0, 1, 0, 0, 96]), bytes([255, 161, 0, 255, 255, 0])),
    )),
    ('Beer Festival B', (2, 0), (
        (4, bytes([1, 95, 1, 1]), bytes([255, 55, 0, 255, 83, 0, 255, 127, 0, 255, 255, 0])),
        (10, bytes([50, 0, 1, 0, 0, 83]), bytes([255, 161, 0, 255, 255, 0])),
    )),
    ('Celebration B', (2, 0), (
        (10, bytes([50, 0, 1, 0, 0, 90]), bytes([255, 255, 255, 255, 255, 255])),
        (8, bytes([5, 3, 90, 0, 29, 1, 0, 0, 0, 0]), bytes([255, 0, 0, 255, 102, 0])),
    )),
    ('Halloween B', (2, 0), (
        (1, bytes([1, 9, 98, 0, 1, 0]), bytes([139, 0, 255, 255, 0, 0])),
        (1, bytes([1, 10, 98, 0, 1, 0]), bytes([255, 127, 0, 0, 0, 255])),
        (10, bytes([50, 0, 1, 0, 0, 90]), bytes([255, 255, 255, 224, 0, 255])),
    )),
    ('Hooray B', (2, 0), (
        (7, bytes([3, 95, 1, 22, 4, 0, 0, 0, 0]), bytes([255, 43, 0, 255, 255, 0, 255, 0, 0])),
        (10, bytes([50, 0, 1, 0, 0, 96]), bytes([255, 161, 0, 255, 255, 0])),
    )),
    ('Wave B', (2, 0), (
        (14, bytes([1, 50, 2, 8, 1, 20, 2, 1]), bytes([255, 0, 0, 255, 127, 0, 255, 255, 0, 0, 255, 0, 0, 0, 255, 0, 255, 255, 139, 0, 255, 255, 255, 255])),
    )),
    ('Crossing B', (2, 0), (
        (14, bytes([1, 50, 2, 8, 1, 20, 2, 0]), bytes([255, 255, 255])),
        (10, bytes([10, 0, 100, 0, 0, 100]), bytes([255, 255, 255])),
        (14, bytes([1, 80, 2, 7, 1, 16, 3, 0]), bytes([255, 0, 0])),
    )),
    ('Flexing B', (2, 0), (
        (15, bytes([1, 50, 1, 2, 4]), bytes([139, 0, 255, 0, 255, 255])),
    )),
    ('Candy Crush B', (2, 0), (
        (16, bytes([3, 50, 0]), bytes([255, 0, 0, 255, 127, 0, 255, 255, 0, 0, 255, 0, 0, 0, 255, 0, 255, 255, 139, 0, 255, 255, 255, 255])),
    )),
    ('Lollipop B', (2, 0), (
        (18, bytes([1, 50, 3]), bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 0, 255, 0, 0, 0, 255, 0, 255, 255, 139, 0, 255, 255, 255, 255])),
    )),
    ('Progress B', (2, 0), (
        (2, bytes([5, 5, 95, 0, 0, 0, 2]), bytes([255, 0, 0, 255, 127, 0, 255, 255, 0, 0, 255, 0, 0, 0, 255])),
        (2, bytes([5, 7, 96, 0, 0, 0, 2]), bytes([0, 255, 255, 139, 0, 255, 255, 0, 0, 255, 127, 0, 255, 255, 0])),
        (10, bytes([1, 0, 200, 0, 0, 100]), bytes([255, 0, 0, 255, 255, 255, 0, 255, 0, 255, 255, 0, 0, 255, 255])),
    )),
    ('Music Show B', None, (
        (4, bytes([7, 96, 1, 1]), bytes([255, 0, 0, 0, 255, 0, 0, 171, 255, 255, 127, 0, 255, 255, 0, 139, 0, 255, 0, 255, 255])),
    )),
    ('Spring B', None, (
        (4, bytes([1, 97, 1, 0]), bytes([255, 0, 0, 0, 255, 0, 0, 171, 255, 255, 127, 0, 255, 255, 0, 139, 0, 255, 0, 255, 255])),
    )),
    ('Rainbow Swirl B', None, (
        (5, bytes([5, 13, 98, 1, 5, 0, 0, 0, 0]), bytes([255, 0, 0, 255, 127, 0, 255, 255, 0, 0, 255, 0, 0, 0, 255, 0, 255, 255, 139, 0, 255])),
    )),
    ('Gradient Circle B', None, (
        (6, bytes([2, 96, 0, 71, 5, 0, 0, 0, 0]), bytes([139, 0, 255, 0, 255, 255, 0, 255, 0, 255, 0, 0])),
    )),
    ('Windmill B', None, (
        (8, bytes([4, 3, 98, 0, 15, 5, 0, 0, 0, 0]), bytes([255, 0, 0, 0, 132, 255, 255, 255, 0])),
    )),
    ('Blossom B', None, (
        (7, bytes([2, 98, 1, 32, 4, 0, 0, 0, 0]), bytes([148, 0, 255, 0, 255, 255, 0, 255, 0, 255, 0, 0])),
    )),
    ('Kaleidoscope B', None, (
        (7, bytes([2, 98, 1, 45, 5, 0, 0, 0, 0]), bytes([255, 0, 0, 0, 255, 0, 0, 0, 255])),
    )),
    ('Fight B', (2, 0), (
        (1, bytes([1, 1, 98, 1, 1, 0]), bytes([255, 0, 0, 255, 0, 0])),
        (1, bytes([1, 2, 98, 1, 1, 0]), bytes([0, 0, 255, 0, 0, 255])),
    )),
    ('Helix B', (2, 0), (
        (9, bytes([5, 23, 93, 1, 1, 1, 100, 0, 0]), bytes([255, 0, 0, 0, 255, 0, 0, 255, 255, 0, 0, 255, 139, 0, 255])),
    )),
    ('Interlaced B', (2, 0), (
        (9, bytes([6, 23, 90, 0, 1, 5, 30, 100, 0]), bytes([255, 0, 0, 143, 45, 255])),
        (9, bytes([3, 23, 90, 0, 1, 4, 30, 100, 140]), bytes([0, 255, 0, 255, 127, 0])),
    )),
    ('Swing B', (2, 0), (
        (2, bytes([4, 6, 94, 0, 1, 0, 1]), bytes([0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 255, 255])),
        (2, bytes([4, 5, 94, 0, 1, 0, 1]), bytes([0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 255, 255])),
    )),
    ('Zigzag B', (2, 0), (
        (9, bytes([7, 23, 90, 0, 1, 2, 0, 100, 0]), bytes([255, 0, 0, 255, 255, 0, 252, 79, 179])),
        (9, bytes([7, 15, 90, 0, 1, 2, 0, 100, 0]), bytes([0, 40, 255, 0, 255, 0, 0, 255, 156])),
    )),
    ('Morning B', (2, 0), (
        (1, bytes([2, 9, 93, 1, 0, 0]), bytes([0, 160, 255, 0, 137, 255, 255, 127, 0])),
    )),
    ('Night B', (2, 0), (
        (9, bytes([7, 24, 40, 1, 0, 5, 15, 100, 0]), bytes([0, 0, 255, 44, 0, 255])),
        (9, bytes([7, 15, 40, 1, 0, 4, 15, 100, 0]), bytes([0, 0, 255, 70, 0, 255])),
        (10, bytes([100, 0, 1, 0, 0, 1]), bytes([0, 255, 255, 106, 255, 255])),
    )),
    ('Afternoon B', (2, 0), (
        (1, bytes([2, 9, 93, 1, 0, 0]), bytes([255, 139, 9, 255, 127, 0, 255, 50, 7])),
    )),
    ('Dinner B', (2, 0), (
        (9, bytes([9, 23, 89, 1, 0, 5, 15, 100, 0]), bytes([255, 181, 53, 255, 127, 0, 255, 144, 30, 255, 83, 0])),
        (10, bytes([50, 0, 1, 0, 0, 37]), bytes([255, 255, 0, 255, 210, 0])),
        (9, bytes([9, 24, 89, 1, 0, 4, 30, 100, 0]), bytes([255, 127, 0, 255, 255, 0])),
    )),
    ('Radiance B', (2, 0), (
        (8, bytes([8, 3, 85, 0, 1, 5, 0, 0, 0, 0]), bytes([255, 0, 0, 255, 255, 0])),
    )),
    ('Sleep B', (2, 0), (
        (1, bytes([2, 9, 70, 1, 0, 0]), bytes([0, 0, 255, 0, 219, 255])),
    )),
    ('Soothing B', (2, 0), (
        (1, bytes([1, 9, 82, 1, 0, 90]), bytes([8, 255, 70, 7, 255, 21, 0, 182, 179, 0, 0, 0])),
    )),
    ('Dreamland B', (2, 0), (
        (1, bytes([6, 3, 95, 1, 0, 0]), bytes([0, 0, 143, 54, 0, 171, 0, 0, 0])),
        (10, bytes([50, 0, 1, 0, 0, 1]), bytes([0, 227, 255, 134, 0, 255])),
    )),
    ('Joyful B', (2, 0), (
        (1, bytes([6, 10, 98, 0, 1, 50]), bytes([255, 127, 0, 255, 53, 0, 98, 255, 0])),
        (1, bytes([6, 9, 98, 0, 1, 50]), bytes([255, 127, 0, 255, 56, 0, 95, 255, 0])),
    )),
    ('Sweet B', (2, 0), (
        (9, bytes([9, 15, 80, 1, 0, 4, 15, 100, 0]), bytes([255, 137, 223, 255, 46, 95, 255, 129, 209, 255, 61, 86])),
        (10, bytes([50, 0, 1, 0, 0, 30]), bytes([255, 90, 255, 255, 229, 255])),
    )),
    ('Leisure B', (2, 0), (
        (9, bytes([10, 23, 80, 1, 0, 1, 100, 15, 0]), bytes([255, 53, 10, 255, 71, 15, 255, 83, 0])),
        (9, bytes([10, 23, 90, 0, 1, 1, 100, 1, 255]), bytes([255, 127, 0, 255, 62, 0])),
    )),
)

BY_NAME = {scene[0]: scene for scene in SCENES}


def _palette(flat: bytes) -> list[list[int]]:
    return [list(flat[i : i + 3]) for i in range(0, len(flat), 3)]


def _raw(block: Block) -> bytes:
    effect, params, flat = block
    return bytes([effect]) + params + bytes([len(flat) // 3]) + flat


def _stock_record(scene: Scene) -> bytes:
    _, header, blocks = scene
    if header is None:
        return _raw(blocks[0])
    out = bytearray([0x0C, len(blocks), *header])
    for block in blocks:
        raw = _raw(block)
        out += bytes([len(raw) & 0xFF, len(raw) >> 8]) + raw
    return bytes(out)


def _upload(record: bytes) -> list[bytes]:
    payload = bytes([len(record) & 0xFF, len(record) >> 8]) + record
    return [*terminated_chunks(payload, H70CX_MARKER), commit_frame(sub_mode=DIY_COMMIT_SUB_MODE, index=714)]


def _service_data(scene: Scene) -> dict[str, Any]:
    _, header, blocks = scene
    fields = [
        {"effect": NAME[effect], "colors": _palette(flat), "params": list(params)} for effect, params, flat in blocks
    ]
    if header is None:
        return fields[0]
    return {"layers": fields, "layer_mode": MODE[header]}


async def _frames(data: dict[str, Any]) -> list[bytes]:
    route = await _run(_coordinator(), _call(**data))
    return list(route.await_args.args[4])


class TestFixture:
    def test_covers_every_spatial_scene(self) -> None:
        assert len(SCENES) == 69
        assert sum(len(blocks) for _, _, blocks in SCENES) == 117
        assert len({(effect, params) for _, _, blocks in SCENES for effect, params, _ in blocks}) == 106

    def test_every_block_has_its_templates_length(self) -> None:
        for _, _, blocks in SCENES:
            for effect, params, _ in blocks:
                assert h70cx.TEMPLATES[NAME[effect]].effect == effect
                assert len(params) == len(h70cx.TEMPLATES[NAME[effect]].params)


class TestEveryStockScene:
    @pytest.mark.parametrize("scene", SCENES, ids=[f"{index}-{scene[0]}" for index, scene in enumerate(SCENES)])
    async def test_replays_byte_for_byte(self, scene: Scene) -> None:
        assert await _frames(_service_data(scene)) == _upload(_stock_record(scene))


class TestNamedStockScenes:
    @pytest.mark.parametrize(
        ("name", "header", "first"),
        [
            ("Universe B", bytes([0x0C, 3, 2, 0]), 6),
            ("Hot Pink B", bytes([0x0C, 3, 2, 0]), 2),
            ("Ocean B", bytes([0x0C, 2, 3, 0]), 14),
            ("Maximalism", bytes([0x0C, 2, 3, 0]), 10),
            ("Fireworks B", bytes([0x0C, 2, 1, 2]), 7),
            ("Birthday B", bytes([0x0C, 2, 2, 0]), 10),
            ("Rustling leaves B", bytes([0x0C, 2, 2, 0]), 1),
            ("Flexing B", bytes([0x0C, 1, 2, 0]), 15),
            ("Candy Crush B", bytes([0x0C, 1, 2, 0]), 16),
        ],
    )
    def test_codec_rebuilds_the_record(self, name: str, header: bytes, first: int) -> None:
        scene = BY_NAME[name]
        data = _service_data(scene)
        layers = [h70cx.Layer(item["effect"], item["colors"], params=item["params"]) for item in data["layers"]]
        built = h70cx.layered_record(layers, data["layer_mode"])

        assert built == _stock_record(scene)
        assert built[:4] == header
        assert built[6] == first

    async def test_recolours_a_stock_scene(self) -> None:
        scene = BY_NAME["Fireworks B"]
        data = _service_data(scene)
        data["layers"][0]["colors"] = [[0, 0, 255], [0, 128, 255], [255, 255, 255]]
        data["layers"][1]["colors"] = [[200, 200, 255]]
        data["layers"][1]["speed"] = 40

        (effect_1, params_1, _), (effect_2, params_2, _) = scene[2]
        expected = _stock_record(
            (
                "",
                (1, 2),
                (
                    (effect_1, params_1, bytes([0, 0, 255, 0, 128, 255, 255, 255, 255])),
                    (effect_2, params_2[:5] + bytes([40]), bytes([200, 200, 255])),
                ),
            )
        )
        assert await _frames(data) == _upload(expected)

    async def test_speed_and_direction_overwrite_a_stock_block(self) -> None:
        _, params, flat = BY_NAME["Birthday B"][2][1]
        frames = await _frames({"effect": "flow", "colors": _palette(flat), "params": list(params), "speed": 9, "direction": "up"})

        assert frames == _upload(bytes([1, params[0], 1, 9]) + params[3:] + bytes([len(flat) // 3]) + flat)


class TestParamsValidation:
    def test_params_are_coerced_bytes(self) -> None:
        data = SERVICE_APPLY_DIY_EFFECT_SCHEMA(
            {"layers": [{"effect": "starlight", "colors": COLORS, "params": ["30", 0, 1, 0, 0, 30]}], "layer_mode": "Variant_3"}
        )
        assert data["layers"][0]["params"] == [30, 0, 1, 0, 0, 30]
        assert data["layer_mode"] == "variant_3"

    @pytest.mark.parametrize(
        "extra",
        [
            {"params": [256, 0, 1, 0, 0, 30]},
            {"params": [-1, 0, 1, 0, 0, 30]},
            {"params": []},
            {"layer_mode": "variant_2"},
        ],
    )
    def test_malformed_input_is_rejected(self, extra: dict[str, Any]) -> None:
        with pytest.raises(vol.Invalid):
            SERVICE_APPLY_DIY_EFFECT_SCHEMA({"effect": "starlight", "colors": COLORS, **extra})

    def test_a_layer_with_an_out_of_range_byte_is_rejected(self) -> None:
        with pytest.raises(vol.Invalid):
            SERVICE_APPLY_DIY_EFFECT_SCHEMA({"layers": [{"effect": "starlight", "colors": COLORS, "params": [300]}]})

    @pytest.mark.parametrize(
        ("data", "reason"),
        [
            ({"effect": "starlight", "params": [30, 0, 1, 0, 0]}, "starlight takes 6 params, not 5"),
            ({"effect": "flow_2", "params": [3, 1, 100, 1, 1, 0, 0]}, "flow_2 takes 6 params, not 7"),
            ({"effect": "wave", "params": [2, 50, 10, 1, 1, 0, 0, 0]}, "the params of wave must start with 1, not 2"),
            ({"effect": "mountain_peak", "params": [1, 50, 10, 1, 1]}, "the params of mountain_peak must start with 2, not 1"),
            ({"effect": "flexing", "params": [3, 50, 1, 2, 4]}, "the params of flexing must start with 1, not 3"),
            ({"effect": "candy_crush", "params": [5, 50, 0]}, "the params of candy_crush must start with 3, not 5"),
            ({"effect": "smudge", "params": [1, 50], "direction": "clockwise"}, "smudge takes only up, down, not 'clockwise'"),
            ({"effect": "flow_1", "params": [1, 2, 3]}, "flow_1 has no params"),
        ],
    )
    async def test_a_bad_block_is_a_translated_error(self, data: dict[str, Any], reason: str) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(**{"colors": COLORS, **data}))
        assert err.value.translation_key == "invalid_string_effect"
        assert err.value.translation_placeholders == {"device": "Light H80C5", "reason": reason}
        assert coordinator.state.active_scene_name == "Rainbow B"

    async def test_a_bad_layer_block_names_the_layer(self) -> None:
        layers = [
            {"effect": "starlight", "colors": COLORS},
            {"effect": "flexing", "colors": COLORS, "params": [2, 50, 1, 2, 4]},
        ]
        with pytest.raises(ServiceValidationError) as err:
            await _run(_coordinator(), _call(layers=layers, layer_mode="variant_1_2"))
        assert err.value.translation_key == "invalid_string_effect_layer"
        assert err.value.translation_placeholders == {
            "device": "Light H80C5",
            "layer": "2",
            "reason": "the params of flexing must start with 1, not 2",
        }

    async def test_layer_mode_without_layers_is_refused(self) -> None:
        coordinator = _coordinator(scene="Rainbow B")
        with pytest.raises(ServiceValidationError) as err:
            await _run(coordinator, _call(effect="flame", colors=COLORS, layer_mode="variant_3"))
        assert err.value.translation_key == "layer_mode_without_layers"
        assert err.value.translation_placeholders == {"device": "Light H80C5"}
        assert coordinator.state.active_scene_name == "Rainbow B"

    async def test_top_level_params_beside_layers_are_refused(self) -> None:
        layers = [{"effect": "starlight", "colors": COLORS}]
        with pytest.raises(ServiceValidationError) as err:
            await _run(_coordinator(), _call(layers=layers, params=[30, 0, 1, 0, 0, 30]))
        assert err.value.translation_key == "layers_with_effect_fields"
        assert err.value.translation_placeholders == {"device": "Light H80C5", "fields": "params"}


class TestCodecParams:
    @pytest.mark.parametrize("params", [[256, 0, 1, 0, 0, 30], [True, 0, 1, 0, 0, 30], ["30", 0, 1, 0, 0, 30]])
    def test_a_block_must_hold_bytes(self, params: list[Any]) -> None:
        with pytest.raises(FrameError, match="params must be values from 0 to 255"):
            h70cx.effect_record("starlight", COLORS, params=params)

    def test_without_params_the_template_block_is_used(self) -> None:
        template = h70cx.TEMPLATES["starlight"]
        assert h70cx.effect_record("starlight", COLORS) == h70cx.effect_record(
            "starlight", COLORS, params=list(template.params)
        )

    def test_shape_and_center_overwrite_a_flexing_block(self) -> None:
        record = h70cx.effect_record("flexing", COLORS[:1], params=[1, 50, 1, 2, 4], shape="round", center="upper_left")
        assert record[:6] == bytes([15, 1, 50, 1, 0, 0])

    def test_an_unknown_layer_mode_is_refused(self) -> None:
        with pytest.raises(FrameError, match="unknown layer mode 'variant_9' \\(have: default, variant_3, variant_1_2\\)"):
            h70cx.layered_record([h70cx.Layer("starlight", COLORS)], "variant_9")

    def test_layer_modes_carry_their_header_bytes(self) -> None:
        layers = [h70cx.Layer("starlight", COLORS)]
        assert [h70cx.layered_record(layers, mode)[:4] for mode in h70cx.LAYER_MODES] == [
            bytes([0x0C, 1, 2, 0]),
            bytes([0x0C, 1, 3, 0]),
            bytes([0x0C, 1, 1, 2]),
        ]

    def test_every_stock_scene_fits_one_message(self) -> None:
        widest = max(SCENES, key=lambda scene: len(_stock_record(scene)))
        assert len(_upload(_stock_record(widest))) <= h70cx.MAX_FRAMES
