"""Create Blender images, materials and a gallery scene from the unpacked PNGs.

Companion to ``texture_library.py``: that module decides *what* to build, this
one talks to ``bpy`` and builds it.

The materials are plain image-plus-shader setups, deliberately unlit: they
connect the texture to an Emission shader so a texture is readable in the
viewport and in EEVEE without any lights in the scene. A gallery of a thousand
surfaces is for inspection, not for rendering.

Nothing here assigns materials to scene geometry. Which texture belongs on
which wall is a decision only the level's author can make, and guessing it
would be worse than leaving it out.
"""
from __future__ import annotations

from pathlib import Path

import bpy

from .texture_library import (BACKFACE_CULLING_DEFAULT, METALLIC,
                              RIM_LIBRARY_PROP, RIM_PATH_PROP, RIM_PNG_PROP,
                              ROUGHNESS, SPECULAR_LEVEL, gallery_placements,
                              material_name, square_grid)

# Name of the collection and scene holding the inspection gallery.
GALLERY_COLLECTION = "RIF Game Textures"
GALLERY_SCENE = "RIF Texture Gallery"

# Gap between gallery planes, in metres. Large enough to see a 128x128 texture
# from above without neighbours touching.
GALLERY_SPACING_M = 2.2


class ImportReport:
    """Tally of one library import."""

    def __init__(self):
        self.materials_created = 0
        self.materials_reused = 0
        self.images_loaded = 0
        self.images_missing = 0
        self.failed = []              # (logical_path, reason)
        self.over_long_names = []

    def __repr__(self):
        return (f"ImportReport(materials={self.materials_created}, "
                f"reused={self.materials_reused}, images={self.images_loaded}, "
                f"missing={self.images_missing}, failed={len(self.failed)})")


def load_image(png_path):
    """Return a Blender image for ``png_path``, reusing one already loaded."""
    png_path = Path(png_path)
    existing = bpy.data.images.get(png_path.name)
    if existing is not None:
        return existing
    image = bpy.data.images.load(str(png_path), check_existing=True)
    image.name = png_path.name
    return image


def build_material(name, image, rim_path):
    """Create one material: image -> Principled BSDF -> Output, culling on.

    A Principled BSDF rather than an Emission shader, because these materials
    are for building levels: an unlit material cannot show how a surface reacts
    to the lights placed around it, which is exactly what an author needs to
    judge. The gallery scene therefore ships with its own light.

    Alpha is wired up when the image has any, so the 43 textures that carry a
    transparent palette index show it; the blend mode only changes in that case
    so opaque textures are not treated as see-through.
    """
    material = bpy.data.materials.new(name=name)
    material.use_nodes = True
    material.use_backface_culling = BACKFACE_CULLING_DEFAULT
    material[RIM_PATH_PROP] = rim_path
    material[RIM_LIBRARY_PROP] = True

    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (400, 0)
    principled = tree.nodes.new("ShaderNodeBsdfPrincipled")
    principled.location = (0, 0)
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.location = (-400, 0)
    texture.image = image
    # Indexed art must not be smoothed, or the palette edges bleed.
    texture.interpolation = "Closest"

    # The engine has no specular and no roughness: a surface is lit diffusely
    # and nothing reflects. Setting these to the values a mirror-free surface
    # needs keeps the Blender preview honest, so a material that looks right
    # here also looks right in the game.
    for socket, value in (("Specular IOR Level", SPECULAR_LEVEL),
                          ("Specular", SPECULAR_LEVEL),
                          ("Roughness", ROUGHNESS),
                          ("Metallic", METALLIC),
                          ("Sheen Weight", 0.0),
                          ("Coat Weight", 0.0),
                          ("Transmission Weight", 0.0),
                          ("Emission Strength", 0.0)):
        if socket in principled.inputs:
            principled.inputs[socket].default_value = value

    tree.links.new(texture.outputs["Color"], principled.inputs["Base Color"])
    tree.links.new(principled.outputs["BSDF"], output.inputs["Surface"])

    if image is not None and image.depth == 32:
        tree.links.new(texture.outputs["Alpha"], principled.inputs["Alpha"])
        # Blender 4.2 took its alpha handling from the material settings.
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "DITHERED"
        elif hasattr(material, "blend_method"):
            material.blend_method = "BLEND"
    elif "Alpha" in principled.inputs:
        # Fully opaque where there is no alpha channel; the socket's own default
        # is 1.0 in Blender, but setting it explicitly keeps the intent visible
        # and survives a future default change.
        principled.inputs["Alpha"].default_value = 1.0
    return material


def find_texture_pngs(unpacked_root):
    """Return ``[(logical_path, png_path)]`` for every PNG under the root.

    The logical path is derived from the file's position, not from its name:
    PNGs sit beside the ``.rim`` they came from, so the relative path plus the
    ``.RIM`` extension reconstructs exactly the name the game expects.
    """
    unpacked_root = Path(unpacked_root)
    found = []
    for png in sorted(unpacked_root.rglob("*")):
        if not png.is_file() or png.suffix.lower() != ".png":
            continue
        relative = png.relative_to(unpacked_root)
        logical = str(relative.with_suffix(".RIM")).replace("/", "\\")
        found.append((logical, png))
    return found


def import_texture_library(unpacked_root, progress=None):
    """Build one material per unpacked texture.

    Returns ``(report, materials)`` where ``materials`` is the list in the order
    the textures were found. Materials already carrying the library marker are
    reused, so running this twice does not produce ``.001`` duplicates.
    """
    report = ImportReport()
    pairs = find_texture_pngs(unpacked_root)
    total = len(pairs)
    materials = []

    for index, (logical, png) in enumerate(pairs, start=1):
        name = material_name(logical)
        if len(logical) > len(name):
            report.over_long_names.append(logical)

        existing = bpy.data.materials.get(name)
        if existing is not None and existing.get(RIM_LIBRARY_PROP):
            materials.append(existing)
            report.materials_reused += 1
        else:
            try:
                image = load_image(png)
            except RuntimeError as exc:
                report.failed.append((logical, f"load failed: {exc}"))
                continue
            if image is None:
                report.images_missing += 1
                report.failed.append((logical, "no image"))
                continue
            if existing is not None:
                # A name clash with an unrelated material: replace it rather
                # than creating "name.001", so the library stays navigable.
                bpy.data.materials.remove(existing)
            material = build_material(name, image, logical)
            material[RIM_PNG_PROP] = str(png)
            report.images_loaded += 1
            report.materials_created += 1
            materials.append(material)

        if progress is not None:
            progress(index, total, logical)

    return report, materials


def _plane_mesh(name, size):
    """Return a quad mesh with a UV layer covering the unit square.

    The UV layer is the point: without one the image has nowhere to map, so a
    material could be seen in the material preview but not on the surface, and
    the gallery would be useless for judging textures.
    """
    mesh = bpy.data.meshes.new(name)
    verts = [(-size, -size, 0.0), (size, -size, 0.0),
             (size, size, 0.0), (-size, size, 0.0)]
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()

    uv_layer = mesh.uv_layers.new(name="UVMap")
    # Corner order matches the vertex order above, so the image lands upright
    # and the right way round when seen from +Z.
    corner_uvs = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))
    for loop_index, uv in zip(range(len(uv_layer.data)), corner_uvs):
        uv_layer.data[loop_index].uv = uv
    return mesh


def _add_gallery_light(collection, count, spacing):
    """Add a sun above the grid so the Principled BSDFs have something to show.

    Without a light every surface renders black, which would make the gallery
    pointless now that the materials are lit rather than emissive.
    """
    light_data = bpy.data.lights.new("GalleryLight", type="SUN")
    light_data.energy = 3.0
    light = bpy.data.objects.new("GalleryLight", light_data)
    light.location = (0.0, 0.0, max(1.0, count ** 0.5) * spacing)
    # Straight down, matching the camera.
    light.rotation_euler = (0.0, 0.0, 0.0)
    collection.objects.link(light)
    return light


def build_gallery_scene(materials, scene_name=GALLERY_SCENE,
                        spacing=GALLERY_SPACING_M):
    """Create a scene of planes, one per material, laid out in a grid.

    Returns the new scene. Each plane carries a UV layer and faces +Z, and the
    camera looks straight down at the grid, which is the most compact way to
    show a thousand textures: press 0 for the camera view.
    """
    scene = bpy.data.scenes.new(scene_name)
    collection = bpy.data.collections.new(GALLERY_COLLECTION)
    scene.collection.children.link(collection)

    columns, _rows = square_grid(len(materials))
    for material, (x, y) in zip(materials, gallery_placements(len(materials),
                                                             spacing,
                                                             columns)):
        mesh = _plane_mesh(f"plane_{material.name}", spacing * 0.45)
        mesh.materials.append(material)

        obj = bpy.data.objects.new(material.name, mesh)
        obj.location = (x, y, 0.0)
        collection.objects.link(obj)

    _add_gallery_light(collection, len(materials), spacing)
    _add_top_down_camera(collection, len(materials), spacing)
    return scene


def _add_top_down_camera(collection, count, spacing):
    """Add an orthographic camera framing the whole grid from above."""
    columns, rows = square_grid(count)
    if not columns:
        return None
    field = max(columns, rows) * spacing
    camera_data = bpy.data.cameras.new("GalleryCamera")
    camera_data.type = "ORTHO"
    camera_data.ortho_scale = field * 1.1
    camera = bpy.data.objects.new("GalleryCamera", camera_data)
    # Straight down: a camera with no rotation looks along -Z, which is what
    # "from above onto the XY plane" means.
    camera.location = (0.0, 0.0, field)
    collection.objects.link(camera)
    return camera


def delete_library():
    """Remove every material and image the import created.

    Returns ``(materials, images)`` counts. Images are only removed when no
    material still references them, so hand-made materials are left alone.
    """
    materials = [m for m in bpy.data.materials if m.get(RIM_LIBRARY_PROP)]
    removed_images = 0
    for material in materials:
        bpy.data.materials.remove(material)
    for image in list(bpy.data.images):
        if image.users == 0:
            bpy.data.images.remove(image)
            removed_images += 1
    return len(materials), removed_images
