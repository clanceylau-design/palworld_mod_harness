from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector


ARM_PREFIXES = ("clavicle_", "upperarm_", "lowerarm_", "hand_")
LEG_PREFIXES = ("thigh_", "calf_", "foot_", "ball_")
HEAD_PREFIXES = ("head", "neck_", "ear_", "hair_")


def parse_args() -> argparse.Namespace:
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(
        description="Generate a fresh semantic UV atlas on a rigged Tripo mesh and rebake UV-independent maps."
    )
    parser.add_argument("--input-blend", required=True)
    parser.add_argument("--mesh", default="BlueCat_TripoNative_surface")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--atlas-size", type=int, default=2048)
    parser.add_argument("--angle-limit-degrees", type=float, default=89.0)
    parser.add_argument("--face-density-scale", type=float, default=1.6)
    parser.add_argument("--pack-margin-pixels", type=int, default=12)
    parser.add_argument(
        "--unwrap-strategy",
        choices=("teacher_charts", "semantic_smart"),
        default="teacher_charts",
    )
    return parser.parse_args(argv)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def mesh_geometry_hash(obj) -> str:
    digest = hashlib.sha256()
    for vertex in obj.data.vertices:
        digest.update(struct.pack("<3f", *vertex.co))
    for polygon in obj.data.polygons:
        digest.update(struct.pack("<I", len(polygon.vertices)))
        digest.update(struct.pack(f"<{len(polygon.vertices)}I", *polygon.vertices))
    return digest.hexdigest()


def mesh_weight_hash(obj) -> str:
    names = {group.index: group.name for group in obj.vertex_groups}
    digest = hashlib.sha256()
    for vertex in obj.data.vertices:
        entries = sorted((names[item.group], item.weight) for item in vertex.groups if item.weight > 1e-8)
        digest.update(struct.pack("<I", vertex.index))
        for name, weight in entries:
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(struct.pack("<f", weight))
    return digest.hexdigest()


def uv_hash(obj, layer_name: str) -> str:
    layer = obj.data.uv_layers[layer_name]
    digest = hashlib.sha256()
    for item in layer.data:
        digest.update(struct.pack("<2f", *item.uv))
    return digest.hexdigest()


def find_material_image(material, prefix: str):
    if material is None or not material.use_nodes:
        raise RuntimeError("Target material must use nodes")
    matches = [
        node.image
        for node in material.node_tree.nodes
        if node.type == "TEX_IMAGE" and node.image and node.image.name.startswith(prefix)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one {prefix} image, found {len(matches)}")
    return matches[0]


def image_pixels(image) -> np.ndarray:
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape((height, width, 4))


def sample_image(pixels: np.ndarray, uv: Vector) -> np.ndarray:
    height, width, _channels = pixels.shape
    x = min(width - 1, max(0, int((uv.x % 1.0) * width)))
    y = min(height - 1, max(0, int((uv.y % 1.0) * height)))
    return pixels[y, x, :3]


def bone_category(name: str) -> str:
    if name.startswith("tail_"):
        return "tail"
    if name.startswith(ARM_PREFIXES):
        return "arm_l" if name.endswith("_l") else "arm_r"
    if name.startswith(LEG_PREFIXES):
        return "leg_l" if name.endswith("_l") else "leg_r"
    if name.startswith(HEAD_PREFIXES):
        return "head"
    return "torso"


def polygon_anatomy(obj, polygon) -> str:
    group_names = {group.index: group.name for group in obj.vertex_groups}
    scores = Counter()
    for vertex_index in polygon.vertices:
        for membership in obj.data.vertices[vertex_index].groups:
            scores[bone_category(group_names[membership.group])] += membership.weight
    return scores.most_common(1)[0][0] if scores else "torso"


def classify_tone(rgb: np.ndarray) -> str:
    r, g, b = (float(value) for value in rgb)
    if r > 0.32 and r > b * 1.15 and g > b * 1.05:
        return "cream"
    if b > 0.12 and b > r * 1.08:
        return "blue"
    return "detail"


def build_semantic_zones(obj, source_uv_name: str, color_image) -> tuple[dict[int, str], dict[str, int]]:
    mesh = obj.data
    uv_layer = mesh.uv_layers[source_uv_name].data
    pixels = image_pixels(color_image)
    anatomy = {}
    tones = {}
    for polygon in mesh.polygons:
        anatomy[polygon.index] = polygon_anatomy(obj, polygon)
        uv = Vector((0.0, 0.0))
        for loop_index in polygon.loop_indices:
            uv += uv_layer[loop_index].uv
        uv /= len(polygon.loop_indices)
        tones[polygon.index] = classify_tone(sample_image(pixels, uv))

    edge_polygons = defaultdict(list)
    for polygon in mesh.polygons:
        for edge_key in polygon.edge_keys:
            edge_polygons[tuple(sorted(edge_key))].append(polygon.index)
    adjacency = defaultdict(set)
    for indices in edge_polygons.values():
        if len(indices) == 2:
            a, b = indices
            adjacency[a].add(b)
            adjacency[b].add(a)

    for _iteration in range(8):
        updates = {}
        for polygon_index, tone in tones.items():
            if tone != "detail":
                continue
            neighbor_tones = [tones[index] for index in adjacency[polygon_index] if tones[index] != "detail"]
            if neighbor_tones:
                winner, count = Counter(neighbor_tones).most_common(1)[0]
                if count >= max(1, math.ceil(len(neighbor_tones) * 0.6)):
                    updates[polygon_index] = winner
        if not updates:
            break
        tones.update(updates)

    zones = {}
    for polygon in mesh.polygons:
        tone = tones[polygon.index]
        zones[polygon.index] = f"{anatomy[polygon.index]}:{tone}"
    return zones, dict(sorted(Counter(zones.values()).items()))


def activate_only(obj) -> None:
    bpy.ops.object.mode_set(mode="OBJECT") if bpy.context.object and bpy.context.object.mode != "OBJECT" else None
    bpy.ops.object.select_all(action="DESELECT")
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def unwrap_zones(
    obj,
    zones: dict[int, str],
    angle_limit_degrees: float,
    atlas_size: int,
    face_scale: float,
    strategy: str,
    pack_margin_pixels: int,
) -> None:
    mesh = obj.data
    activate_only(obj)
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    if strategy == "semantic_smart":
        work_groups = [
            (zone, {index for index, value in zones.items() if value == zone})
            for zone in sorted(set(zones.values()))
        ]
    else:
        work_groups = [("teacher_charts", set(zones))]

    for _label, polygon_indices in work_groups:
        for polygon in mesh.polygons:
            polygon.select = polygon.index in polygon_indices
        bpy.ops.object.mode_set(mode="EDIT")
        if strategy == "semantic_smart":
            bpy.ops.uv.smart_project(
                angle_limit=math.radians(angle_limit_degrees),
                island_margin=0.0005,
                area_weight=0.0,
                correct_aspect=True,
                scale_to_bounds=False,
            )
        else:
            bpy.ops.mesh.select_all(action="SELECT")
            bpy.ops.uv.unwrap(
                method="ANGLE_BASED",
                fill_holes=True,
                correct_aspect=True,
                margin=0.0005,
            )
        bpy.ops.object.mode_set(mode="OBJECT")

    for polygon in mesh.polygons:
        polygon.select = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.average_islands_scale()
    bpy.ops.object.mode_set(mode="OBJECT")

    face_loops = [
        loop_index
        for polygon in mesh.polygons
        if zones[polygon.index] == "head:cream"
        for loop_index in polygon.loop_indices
    ]
    if face_loops:
        layer = mesh.uv_layers.active.data
        center = sum((layer[index].uv for index in face_loops), Vector((0.0, 0.0))) / len(face_loops)
        for loop_index in face_loops:
            layer[loop_index].uv = center + (layer[loop_index].uv - center) * face_scale

    for polygon in mesh.polygons:
        polygon.select = True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.select_all(action="SELECT")
    bpy.ops.uv.pack_islands(rotate=True, margin=pack_margin_pixels / atlas_size)
    bpy.ops.object.mode_set(mode="OBJECT")
    mesh.update()


def uv_chart_stats(mesh) -> dict:
    mesh.calc_loop_triangles()
    loop_count = len(mesh.loops)
    triangle_count = len(mesh.loop_triangles)
    uv = np.empty(loop_count * 2, dtype=np.float64)
    mesh.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape((-1, 2))
    tri_loops = np.empty(triangle_count * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    tri_loops = tri_loops.reshape((-1, 3))
    quantized = np.rint(uv * 10000000.0).astype(np.int64)
    unique_uv, inverse = np.unique(quantized, axis=0, return_inverse=True)
    triangle_uv_ids = inverse[tri_loops]
    edges = np.concatenate(
        (
            triangle_uv_ids[:, (0, 1)],
            triangle_uv_ids[:, (1, 2)],
            triangle_uv_ids[:, (2, 0)],
        ),
        axis=0,
    ).astype(np.int32, copy=False)
    labels = np.arange(len(unique_uv), dtype=np.int32)
    converged = False
    iterations = 0
    for iterations in range(1, 65):
        before = labels.copy()
        a, b = edges[:, 0], edges[:, 1]
        high = np.maximum(labels[a], labels[b])
        low = np.minimum(labels[a], labels[b])
        np.minimum.at(labels, high, low)
        for _ in range(8):
            compressed = labels[labels]
            if np.array_equal(compressed, labels):
                break
            labels = compressed
        if np.array_equal(labels, before):
            converged = True
            break
    roots, triangle_counts = np.unique(labels[triangle_uv_ids[:, 0]], return_counts=True)
    triangle_counts.sort()

    triangle_uv = uv[tri_loops]
    e1 = triangle_uv[:, 1] - triangle_uv[:, 0]
    e2 = triangle_uv[:, 2] - triangle_uv[:, 0]
    areas = np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]) * 0.5
    return {
        "charts": int(len(roots)),
        "medianTrianglesPerChart": float(np.median(triangle_counts)),
        "largestChartTriangleFraction": float(triangle_counts[-1] / triangle_count),
        "singletonCharts": int((triangle_counts == 1).sum()),
        "summedUvTriangleArea": float(areas.sum()),
        "degenerateUvTriangleFraction": float((areas <= 1e-16).mean()),
        "uvBounds": {
            "min": [float(value) for value in uv.min(axis=0)],
            "max": [float(value) for value in uv.max(axis=0)],
        },
        "converged": converged,
        "iterations": iterations,
    }


def weighted_percentile(values: np.ndarray, weights: np.ndarray, percentile: float) -> float:
    order = np.argsort(values)
    values = values[order]
    weights = weights[order]
    cutoff = float(weights.sum()) * percentile / 100.0
    return float(values[np.searchsorted(np.cumsum(weights), cutoff, side="left")])


def distortion_stats(obj, zones: dict[int, str]) -> dict:
    mesh = obj.data
    mesh.calc_loop_triangles()
    coordinates = np.array([obj.matrix_world @ vertex.co for vertex in mesh.vertices], dtype=np.float64)
    uv_layer = mesh.uv_layers.active.data
    anisotropy_values = []
    area_weights = []
    density_by_zone = defaultdict(lambda: [0.0, 0.0])
    degenerate = 0
    for triangle in mesh.loop_triangles:
        vertex_indices = triangle.vertices
        loop_indices = triangle.loops
        p0, p1, p2 = (coordinates[index] for index in vertex_indices)
        t0, t1, t2 = (np.array(uv_layer[index].uv, dtype=np.float64) for index in loop_indices)
        e1 = p1 - p0
        e2 = p2 - p0
        du1 = t1 - t0
        du2 = t2 - t0
        geom_area = float(np.linalg.norm(np.cross(e1, e2)) * 0.5)
        uv_area = float(abs(du1[0] * du2[1] - du1[1] * du2[0]) * 0.5)
        if geom_area <= 1e-16 or uv_area <= 1e-16:
            degenerate += 1
            continue
        zone = zones[triangle.polygon_index]
        density_by_zone[zone][0] += uv_area
        density_by_zone[zone][1] += geom_area
        x1 = float(np.linalg.norm(e1))
        x2 = float(np.dot(e2, e1) / x1)
        y2 = math.sqrt(max(float(np.dot(e2, e2)) - x2 * x2, 1e-30))
        m00 = du1[0] / x1
        m10 = du1[1] / x1
        m01 = (du2[0] - m00 * x2) / y2
        m11 = (du2[1] - m10 * x2) / y2
        ata00 = m00 * m00 + m10 * m10
        ata01 = m00 * m01 + m10 * m11
        ata11 = m01 * m01 + m11 * m11
        trace = ata00 + ata11
        disc = math.sqrt(max((ata00 - ata11) ** 2 + 4.0 * ata01 * ata01, 0.0))
        largest = max((trace + disc) * 0.5, 1e-30)
        smallest = max((trace - disc) * 0.5, 1e-30)
        anisotropy_values.append(math.sqrt(largest / smallest))
        area_weights.append(geom_area)
    values = np.array(anisotropy_values, dtype=np.float64)
    weights = np.array(area_weights, dtype=np.float64)
    densities = {
        zone: math.sqrt(uv_area / geom_area)
        for zone, (uv_area, geom_area) in density_by_zone.items()
        if geom_area > 1e-16
    }
    body_values = [value for zone, value in densities.items() if zone != "head:cream"]
    return {
        "anisotropyP50": weighted_percentile(values, weights, 50),
        "anisotropyP90": weighted_percentile(values, weights, 90),
        "anisotropyP99": weighted_percentile(values, weights, 99),
        "degenerateTriangleFraction": degenerate / max(len(mesh.loop_triangles), 1),
        "densityBySemanticZone": dict(sorted(densities.items())),
        "faceToOtherZoneMedianDensityRatio": (
            densities.get("head:cream", 0.0) / np.median(body_values) if body_values else 0.0
        ),
    }


def create_transfer_material(name: str, source_image, target_image, source_uv_name: str):
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    uv_map = nodes.new("ShaderNodeUVMap")
    uv_map.uv_map = source_uv_name
    source = nodes.new("ShaderNodeTexImage")
    source.image = source_image
    emission = nodes.new("ShaderNodeEmission")
    output = nodes.new("ShaderNodeOutputMaterial")
    target = nodes.new("ShaderNodeTexImage")
    target.image = target_image
    target.select = True
    nodes.active = target
    material.node_tree.links.new(uv_map.outputs["UV"], source.inputs["Vector"])
    material.node_tree.links.new(source.outputs["Color"], emission.inputs["Color"])
    material.node_tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
    return material


def bake_uv_transfer(obj, source_image, source_uv_name: str, output_path: Path, atlas_size: int, non_color: bool):
    target_image = bpy.data.images.new(
        output_path.stem, width=atlas_size, height=atlas_size, alpha=False, float_buffer=False
    )
    target_image.generated_color = (0.0, 0.0, 0.0, 1.0)
    target_image.colorspace_settings.name = "Non-Color" if non_color else "sRGB"
    target_image.file_format = "PNG"
    target_image.filepath_raw = str(output_path)
    material = create_transfer_material(f"Bake_{output_path.stem}", source_image, target_image, source_uv_name)
    obj.data.materials.clear()
    obj.data.materials.append(material)
    activate_only(obj)
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.cycles.device = "CPU"
    bpy.context.scene.cycles.samples = 1
    bpy.ops.object.bake(type="EMIT", margin=16, use_clear=True)
    target_image.save()
    return target_image


def texture_transfer_error(obj, source_uv_name: str, target_uv_name: str, source_image, target_image) -> dict:
    source_pixels = image_pixels(source_image)
    target_pixels = image_pixels(target_image)
    source_uv = obj.data.uv_layers[source_uv_name].data
    target_uv = obj.data.uv_layers[target_uv_name].data
    errors = []
    for polygon in obj.data.polygons:
        source_center = Vector((0.0, 0.0))
        target_center = Vector((0.0, 0.0))
        for loop_index in polygon.loop_indices:
            source_center += source_uv[loop_index].uv
            target_center += target_uv[loop_index].uv
        source_center /= len(polygon.loop_indices)
        target_center /= len(polygon.loop_indices)
        difference = sample_image(source_pixels, source_center) - sample_image(target_pixels, target_center)
        errors.append(float(np.linalg.norm(difference)))
    values = np.array(errors, dtype=np.float64)
    return {
        "triangleCentroidRgbEuclideanMean": float(values.mean()),
        "triangleCentroidRgbEuclideanP95": float(np.percentile(values, 95)),
        "triangleCentroidRgbEuclideanMax": float(values.max()),
        "triangleFractionAbove0_08": float((values > 0.08).mean()),
        "sampleCount": int(len(values)),
    }


def create_preview_material(base_color, orm):
    material = bpy.data.materials.new("M_BlueCat_SemanticUVV1")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    base = nodes.new("ShaderNodeTexImage")
    base.image = base_color
    packed = nodes.new("ShaderNodeTexImage")
    packed.image = orm
    packed.image.colorspace_settings.name = "Non-Color"
    separate = nodes.new("ShaderNodeSeparateColor")
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    output = nodes.new("ShaderNodeOutputMaterial")
    material.node_tree.links.new(base.outputs["Color"], shader.inputs["Base Color"])
    material.node_tree.links.new(packed.outputs["Color"], separate.inputs["Color"])
    material.node_tree.links.new(separate.outputs["Green"], shader.inputs["Roughness"])
    material.node_tree.links.new(separate.outputs["Blue"], shader.inputs["Metallic"])
    material.node_tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    return material


def point_at(camera, target: Vector) -> None:
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()


def clear_pose(rig) -> None:
    if rig is None:
        return
    for bone in rig.pose.bones:
        bone.rotation_mode = "XYZ"
        bone.rotation_euler = (0.0, 0.0, 0.0)
        bone.location = (0.0, 0.0, 0.0)
        bone.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()


def apply_test_pose(rig) -> None:
    clear_pose(rig)
    rotations = {
        "upperarm_l": (math.radians(8), math.radians(-18), math.radians(55)),
        "lowerarm_l": (0.0, math.radians(-25), math.radians(18)),
        "upperarm_r": (math.radians(-8), math.radians(18), math.radians(-55)),
        "lowerarm_r": (0.0, math.radians(25), math.radians(-18)),
        "thigh_l": (math.radians(28), 0.0, math.radians(8)),
        "calf_l": (math.radians(-32), 0.0, 0.0),
        "thigh_r": (math.radians(-22), 0.0, math.radians(-8)),
        "calf_r": (math.radians(24), 0.0, 0.0),
        "spine_03": (math.radians(8), math.radians(-7), math.radians(10)),
        "head": (math.radians(-6), math.radians(10), math.radians(-14)),
        "ear_02_l": (0.0, math.radians(-12), math.radians(8)),
        "ear_02_r": (0.0, math.radians(12), math.radians(-8)),
    }
    if rig:
        for name, rotation in rotations.items():
            if name in rig.pose.bones:
                rig.pose.bones[name].rotation_euler = rotation
    bpy.context.view_layer.update()


def render_previews(obj, output_dir: Path, prefix: str) -> dict:
    for scene_object in bpy.context.scene.objects:
        if scene_object.type == "MESH":
            scene_object.hide_render = scene_object != obj
    rig = next((item for item in bpy.context.scene.objects if item.type == "ARMATURE"), None)
    camera = bpy.data.objects.get("SemanticUVCamera")
    if camera is None:
        camera_data = bpy.data.cameras.new("SemanticUVCamera")
        camera = bpy.data.objects.new("SemanticUVCamera", camera_data)
        bpy.context.collection.objects.link(camera)
    bpy.context.scene.camera = camera
    camera.data.lens = 58

    if not bpy.data.objects.get("SemanticUVKey"):
        for name, location, energy, size in [
            ("SemanticUVKey", (2.8, 2.6, 3.5), 700.0, 4.0),
            ("SemanticUVFill", (-2.8, 1.8, 2.0), 450.0, 3.0),
            ("SemanticUVRim", (0.0, -2.8, 2.6), 600.0, 3.0),
        ]:
            data = bpy.data.lights.new(name, "AREA")
            data.energy = energy
            data.shape = "DISK"
            data.size = size
            light = bpy.data.objects.new(name, data)
            light.location = location
            point_at(light, Vector((0.0, 0.0, 0.5)))
            bpy.context.collection.objects.link(light)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE_NEXT"
    scene.render.resolution_x = 512
    scene.render.resolution_y = 512
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.world.color = (0.012, 0.018, 0.028)
    outputs = {}
    for name, location in {
        "front": (0.0, 2.8, 0.58),
        "three-quarter": (1.9, 2.35, 0.95),
        "back": (0.0, -2.8, 0.58),
    }.items():
        camera.location = location
        point_at(camera, Vector((0.0, 0.0, 0.52)))
        path = output_dir / f"{prefix}-{name}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        outputs[name] = {"path": str(path), "sha256": sha256(path)}
    return outputs


def export_uv_layout(obj, output_path: Path) -> None:
    mesh = obj.data
    mesh.calc_loop_triangles()
    triangle_count = len(mesh.loop_triangles)
    uv = np.empty(len(mesh.loops) * 2, dtype=np.float64)
    mesh.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape((-1, 2))
    triangle_loops = np.empty(triangle_count * 3, dtype=np.int32)
    mesh.loop_triangles.foreach_get("loops", triangle_loops)
    triangle_uv = uv[triangle_loops.reshape((-1, 3))]
    size = 1024
    raster = np.ones((size, size, 4), dtype=np.float32)
    raster[:, :, :3] = 0.97
    for a, b in ((0, 1), (1, 2), (2, 0)):
        start, end = triangle_uv[:, a], triangle_uv[:, b]
        for t in np.linspace(0.0, 1.0, 16):
            points = start * (1.0 - t) + end * t
            x = np.clip(np.rint(points[:, 0] * (size - 1)).astype(np.int32), 0, size - 1)
            y = np.clip(np.rint(points[:, 1] * (size - 1)).astype(np.int32), 0, size - 1)
            raster[y, x, :3] = 0.03
    image = bpy.data.images.new("Semantic UV Layout", width=size, height=size, alpha=True)
    image.pixels.foreach_set(raster.reshape(-1))
    image.file_format = "PNG"
    image.filepath_raw = str(output_path)
    image.save()


def main() -> None:
    args = parse_args()
    input_blend = Path(args.input_blend).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.open_mainfile(filepath=str(input_blend))
    source = bpy.data.objects.get(args.mesh)
    if source is None or source.type != "MESH":
        raise RuntimeError(f"Mesh not found: {args.mesh}")
    if len(source.data.uv_layers) != 1 or len(source.data.materials) != 1:
        raise RuntimeError("Expected one source UV layer and one material")

    source_material = source.data.materials[0]
    source_color = find_material_image(source_material, "Color_")
    source_orm = find_material_image(source_material, "ORM_")
    target = source.copy()
    target.data = source.data.copy()
    target.name = "BlueCat_TripoNative_SemanticUVV1"
    bpy.context.collection.objects.link(target)
    source.hide_set(True)
    source.hide_render = True
    target.hide_set(False)
    target.hide_render = False

    geometry_before = mesh_geometry_hash(target)
    weights_before = mesh_weight_hash(target)
    target.data.uv_layers[0].name = "TripoSourceUV"
    source_uv_hash = uv_hash(target, "TripoSourceUV")
    semantic_uv = target.data.uv_layers.new(name="SemanticUV")
    target.data.uv_layers.active_index = len(target.data.uv_layers) - 1
    semantic_uv.active_render = True
    target.data.uv_layers["TripoSourceUV"].active_render = False

    zones, zone_counts = build_semantic_zones(target, "TripoSourceUV", source_color)
    unwrap_zones(
        target,
        zones,
        angle_limit_degrees=args.angle_limit_degrees,
        atlas_size=args.atlas_size,
        face_scale=args.face_density_scale,
        strategy=args.unwrap_strategy,
        pack_margin_pixels=args.pack_margin_pixels,
    )
    new_uv_hash = uv_hash(target, "SemanticUV")
    chart_report = uv_chart_stats(target.data)
    distortion_report = distortion_stats(target, zones)

    base_color_path = output_dir / "T_BlueCat_BaseColor_SemanticUVV1.png"
    orm_path = output_dir / "T_BlueCat_ORM_SemanticUVV1.png"
    baked_base = bake_uv_transfer(
        target, source_color, "TripoSourceUV", base_color_path, args.atlas_size, non_color=False
    )
    baked_orm = bake_uv_transfer(target, source_orm, "TripoSourceUV", orm_path, args.atlas_size, non_color=True)
    transfer_error = texture_transfer_error(
        target, "TripoSourceUV", "SemanticUV", source_color, baked_base
    )
    target.data.materials.clear()
    target.data.materials.append(create_preview_material(baked_base, baked_orm))
    target.data.uv_layers.remove(target.data.uv_layers["TripoSourceUV"])
    target.data.uv_layers.active_index = 0
    target.data.uv_layers[0].active_render = True

    geometry_after = mesh_geometry_hash(target)
    weights_after = mesh_weight_hash(target)
    layout_path = output_dir / "semantic-uv-layout.png"
    export_uv_layout(target, layout_path)
    rig = next((item for item in bpy.context.scene.objects if item.type == "ARMATURE"), None)
    clear_pose(rig)
    rest_renders = render_previews(target, output_dir, "semantic-uv-rest")
    apply_test_pose(rig)
    stress_renders = render_previews(target, output_dir, "semantic-uv-stress")
    clear_pose(rig)
    final_blend = output_dir / "BlueCat-TripoNative-SemanticUVV1.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(final_blend), check_existing=False)

    checks = {
        "sourceUvNotReused": source_uv_hash != new_uv_hash,
        "onlySemanticUvInFinalMesh": [layer.name for layer in target.data.uv_layers] == ["SemanticUV"],
        "geometryUnchanged": geometry_before == geometry_after,
        "weightsUnchanged": weights_before == weights_after,
        "chartsAtMost80": chart_report["charts"] <= 80,
        "singletonChartsAtMostInheritedThree": chart_report["singletonCharts"] <= 3,
        "atlasAreaAtLeast55Percent": chart_report["summedUvTriangleArea"] >= 0.55,
        "noDegenerateUvTriangles": chart_report["degenerateUvTriangleFraction"] == 0.0,
        "anisotropyP90AtMost1_5": distortion_report["anisotropyP90"] <= 1.5,
        "faceDensityAtLeast1_25x": distortion_report["faceToOtherZoneMedianDensityRatio"] >= 1.25,
        "baseColorTransferP95AtMost0_08": transfer_error["triangleCentroidRgbEuclideanP95"] <= 0.08,
        "baseColorOutlierFractionAtMost1_5Percent": transfer_error["triangleFractionAbove0_08"] <= 0.015,
        "baseColorWritten": base_color_path.is_file(),
        "ormWritten": orm_path.is_file(),
    }
    checks = {name: bool(value) for name, value in checks.items()}
    report = {
        "schemaVersion": 1,
        "artifactType": "SemanticUvRegenerationExperiment",
        "status": "blender_static_semantic_uv_passed" if all(checks.values()) else "semantic_uv_quality_gate_failed",
        "gameBuildId": "24575825",
        "target": "PinkCat",
        "method": {
            "surface": "existing 29,999-triangle Tripo-native rig candidate",
            "semanticPartition": "original PinkCat dominant bone regions plus broad cream/blue surface classes",
            "unwrap": (
                "fresh Angle Based parameterization on Tripo teacher chart topology, then global average scale, face density boost and repack"
                if args.unwrap_strategy == "teacher_charts"
                else "per-semantic-zone Smart Project with high angle limit, global average scale, face density boost and global pack"
            ),
            "textureTransfer": "Cycles emission bake from temporary read-only Tripo UV into fresh SemanticUV",
            "normalMap": "not transferred because tangent-space normals are invalid after changing UV tangents; requires a new high-to-low tangent bake",
        },
        "inputs": {
            "blend": {"path": str(input_blend), "sha256": sha256(input_blend)},
            "mesh": args.mesh,
            "sourceUvHash": source_uv_hash,
        },
        "parameters": {
            "atlasSize": args.atlas_size,
            "angleLimitDegrees": args.angle_limit_degrees,
            "faceDensityScale": args.face_density_scale,
            "unwrapStrategy": args.unwrap_strategy,
            "packMarginPixels": args.pack_margin_pixels,
            "bakeMarginPixels": 16,
        },
        "geometry": {
            "vertices": len(target.data.vertices),
            "polygons": len(target.data.polygons),
            "geometryHashBefore": geometry_before,
            "geometryHashAfter": geometry_after,
            "weightHashBefore": weights_before,
            "weightHashAfter": weights_after,
            "finalUvHash": new_uv_hash,
        },
        "semanticZones": zone_counts,
        "uv": chart_report,
        "distortion": distortion_report,
        "baseColorTransferError": transfer_error,
        "checks": checks,
        "outputs": {
            "blend": {"path": str(final_blend), "sha256": sha256(final_blend)},
            "baseColor": {"path": str(base_color_path), "sha256": sha256(base_color_path)},
            "orm": {"path": str(orm_path), "sha256": sha256(orm_path)},
            "uvLayout": {"path": str(layout_path), "sha256": sha256(layout_path)},
            "renders": {"rest": rest_renders, "syntheticStress": stress_renders},
        },
        "evidenceBoundary": "Fresh UV topology, Base Color/ORM transfer, unchanged rig weights, Blender rest and synthetic stress renders. Tangent normal rebake, real animation regression, Unreal import/cook and Palworld runtime are separate gates.",
    }
    report_path = output_dir / "semantic-uv-report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["status"] != "blender_static_semantic_uv_passed":
        raise RuntimeError("Semantic UV experiment did not pass all quality gates")


if __name__ == "__main__":
    main()
