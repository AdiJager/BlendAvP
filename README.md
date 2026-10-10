# RIF Exporter

A Blender extension that exports a scene to the `.rif` format used by
**Aliens versus Predator (1999)** — geometry, collisions, lighting, textures,
multiplayer spawn points and enemy generators.

| | |
|---|---|
| Blender | 4.2.0 or newer |
| License | GPL-3.0-or-later |
| Game | Aliens versus Predator Classic (1999) |

## Installing and enabling

1. `Edit > Preferences > Add-ons > Install...` and point it at the extension
   package.
2. Enable **RIF Exporter** in the add-on list.

## Building the extension package

```
python tools/build_extension.py
```

This writes `builds/rif_exporter-<version>.zip`, ready to install. Use
`--check` to validate without writing anything, `--out DIR` to put the archive
elsewhere, and `--name NAME` to change its base name.

Blender looks for `blender_manifest.toml` at the **root** of the archive, so a
zip that wraps everything in a subfolder is rejected as an extension. The tool
builds a flat archive and verifies that before reporting success.

When Blender is installed the build is delegated to its own
`blender --command extension build`, which is authoritative. Blender is found
automatically, including Steam installs; override it with `--blender PATH` or
the `BLENDER` environment variable. On a machine without Blender the tool falls
back to a pure-Python build that produces an equivalent archive — pass
`--no-blender` to force that path.

## Placing AvP gameplay objects

`Object > Add > AvP` holds everything the game needs beyond geometry. The menu
is grouped **by category first**, so the first choice answers "where does this
work in the game", not "which chunk is it".

| Category | What it places |
|---|---|
| **Player Start** | where the player begins: a single-player start, or multiplayer spawn points |
| **NPC Generator** | where enemies come from — one fixed creature, the level's default, or a chosen mix |
| **Navigation** | alien waypoints and the patrol paths humans walk |
| **Gameplay** | the level's global generator settings |

### Multiplayer spawn points

For a deathmatch, coop or skirmish map you place **Multiplayer Spawn** points —
six is the usual number. Each one has three race toggles in
`Properties > Object > AvP Placeable`:

- **Marine** and **Predator** are what skirmish allows. Both on is the most
  common combination in the shipped levels.
- **Alien** is off by default. Skirmish forbids the alien player outright, so a
  spawn that allows *only* the alien can never place anyone. The exporter warns
  when it sees one.

A single-player **Player Start** is not required on a skirmish map — two thirds
of the shipped multiplayer levels have none. Place one only if you also want to
play the level in single player.

### NPC generators

Three of the generator entries work in a network game: **Alien Drone**,
**Predalien** and **Praetorian**. The others are marked
`(single player)` in the menu because the engine refuses to spawn them in
skirmish or coop — placing them on a network map has no effect.

Those three appear as soon as the level loads. The remaining two entries below
are different in kind: they are *runtime* spawners, and the engine deliberately
will not create an NPC while the generator's module is visible to the player —
enemies are meant to arrive from somewhere you cannot see.

A module is one mesh object in the level. **A level built from a single object
therefore has a single module, and a runtime generator in it will never fire**,
because the player is standing inside the only module there is. To use them,
split the level into at least two separate objects and place the generator where
the player cannot see it.

### The spawning budget

A level with generators but no spawning budget stays **empty**, however correct
the generators are. The exporter adds a budget automatically as soon as the
level contains any generator that is not a player spawn, so normally there is
nothing to do. Place **Global Generator Data** when you want to control it:
the NPC cap, the rate, the acceleration, the hive timing, and which creature
generators without weights fall back to.

Never place two — the game reads a single one.

### Modules, and why a generator may stay silent

A level is divided into **modules**, one per mesh object. Modules are what the
game's visibility and AI navigation work in, and those are **two separate
things**:

- **Sight** — you declare it, by parenting modules to each other in the
  **Outliner**. A module sees **itself, its parent and its children**.
- **Travel** — the exporter builds this from geometry. Modules whose boxes meet
  are linked, so NPCs can walk between them. Modules touching only along an edge
  or a corner are not linked, so NPCs cannot path through a wall.

**Parenting is the visibility graph.** One drag in the Outliner links two spaces
both ways, which is the usual case — "these two rooms are connected".

A corridor of rooms is a tree. The middle room is the parent:

```
Red
 ├── Green
 └── Vent_01
      └── Vent_02
```

`Red` sees `Green` and `Vent_01`; `Vent_01` also sees `Vent_02`. If you want
`Red` to see straight down the shaft into `Vent_02`, parent `Vent_02` to `Red`.

> **A module with no parent and no children renders on its own.** You will see
> only the room you are standing in. The exporter warns when a level has several
> modules and nothing linked, because that is easy to miss.

#### Hiding a generator

The game deliberately **will not create an NPC while the generator's module is
visible**. Enemies are meant to arrive from somewhere you cannot see.

So a runtime generator — **Mixed Enemy** or **Default Enemy** — only fires in a
module nothing links to. **Leave that module out of the tree** and it is
invisible from everywhere:

```
Red
 ├── Green
 └── Vent_01          <- the way in
Vent_02               <- unlinked: generator here
```

There is no checkbox for this, and that is on purpose: what makes a generator
work is the same thing that stops the module being drawn, so a hidden room is
hidden from the eye too. Keep the generator's module somewhere the player
genuinely cannot see — around a corner, or behind the module they can see — and
its absence will not be noticeable.

A level built from a single object has a single module, so anything in it is
always visible and no runtime generator can ever fire. Split such a level into
at least two objects.

Fixed generators — **Alien Drone**, **Predalien**, **Praetorian** — are not
affected by any of this. They are created when the level loads, which is why
they work anywhere.

## Where the **RIF Light** panel is

Light settings that Blender has no equivalent for — reach in game units, a
brightness multiplier, viewport preview — live **on the light object, not in the
export dialog**. To find them:

1. In the viewport, **select a light object** (Point or Spot).
2. Open the **Properties** editor and the **Object** tab — the first icon in the
   vertical bar on the left (an orange square).
3. The **RIF Light** panel is at the top of the panel stack.

The panel appears **only for Point/Spot lights**. With a mesh, camera or Sun
selected it is absent, which is expected.

> Cannot find the lights in your scene? `Object > Debug > Select RIF Lights`
> selects them all and reports how many there are.

## What the panel contains

| Field | Meaning |
|---|---|
| **Reach (m)** | how far the light reaches; `0` = the 25 m default |
| **Brightness Scale** | a game-only multiplier on power; `0`/`1` = unchanged |
| **Show Reach in Viewport** | draws the light's reach in the viewport |

Below that the panel reports **live** what the exporter will write:

```
Power: 500 W
Engine brightness: 46592
```

If the light has hit the ceiling you will see the warning
`At the ceiling - raise Reach or lower Power`.

## Reach visualisation

With **Show Reach in Viewport** enabled, each light draws its own zone in the
viewport:

- **Point** — a wireframe sphere: the full reach plus rings at 2/3 and 1/3, so
  you can see which zone a wall falls into;
- **Spot** — a cone at the angle taken from `Spot Size`.

The selected light draws clearly, the rest faintly.

## When the reach does not appear

Blender **swallows errors raised while drawing in the viewport** — a crash looks
exactly like the preview simply being switched off. That is why there is a
diagnostic tool:

**`Object > Debug > Diagnose RIF Reach Overlay`**

It prints to the console (`Window > Toggle System Console`) and to the UI:

- whether the preview is registered at all,
- how many lights are in the active view layer,
- which lights qualify for drawing — with their `range_m` and `show_reach`,
- **the last drawing error**, if there was one.

Check two things first:

1. **`Reach` is non-zero.** `Reach = 0` means "use the 25 m default" and does
   draw, but if you set a very small reach the circle may be smaller than you
   expect — zoom out.
2. **`Show Reach in Viewport` is on** *for that light* — this is a per-object
   setting, not a global one.

## Textures from the game

The game keeps its textures in the compressed archives `Tex1.FFL`…`Tex58.FFL`
(1028 `.RIM` files). The extension can unpack them, convert them to `.png` and
build a **material library** ready to use on your levels.

### Pointing at the game directory

1. `Edit > Preferences > Add-ons`, find **RIF Exporter** and expand the arrow
   next to its name (this opens the extension's settings).
2. In **Game Directory**, point at the game folder — the one containing the
   `fastfile` and `avp_rifs` subfolders.

### Load Game Textures

The **Load Game Textures** button does everything in sequence:

1. reads the archives `Tex1.FFL`…`Tex58.FFL` (sound archives are skipped),
2. unpacks them to `<game directory>\fastfile\_ffl_unpacked`,
3. converts each `.RIM` to a `.png` beside it,
4. creates **one material per texture**.

The operation runs once and takes a while. It is safe to interrupt and restart —
whatever is already done is skipped.

### How the materials are named

A material's name is the texture's **in-game path**, e.g.
`Envrnmts\Battle\Cliff04.RIM`. That makes the source level obvious at a glance,
and the names are unique — 109 file names in the game repeat across directories
**with different artwork**, so the bare file name would be ambiguous.

Every material has **backface culling** enabled, so the preview matches how the
game draws walls.

### The material gallery

The **Build Texture Gallery** button creates a separate scene holding a grid of
planes, each with a different material. Press `0` to enter camera view and
browse the whole library. It is the quickest way to find a texture by eye.

**Clear Texture Library** removes the game materials and any unused images, for
when you want to start over.

### Assigning textures to walls

The extension **does not assign textures to geometry** — there is no way to
predict which texture belongs on which surface. You apply materials yourself,
as in any other Blender project. On export each material becomes a separate
entry in the file, so different walls can carry different textures.

### What happens without game textures

A material with no game texture still exports — under its own name. If the game
cannot find that texture, **the walls turn black and stop responding to light**
(the engine degrades them to unlit polygons). This is the commonest cause of
"dark geometry", and it is not a lack of lights.

## Where the lighting comes from

| What | Where in Blender |
|---|---|
| ambient light | `World > Color` × `World > Strength` |
| a lamp's brightness and colour | the light's `Power`, `Color`, `Temperature` |
| reach | `RIF Light > Reach` |
| cone | `Spot Size` + `Spot Blend` |

**Watch out for `World > Strength`.** It is the brightness of the entire
environment: at `1.0` the level comes out uniformly bright and no light can show
through. Sensible values for a level are **0.05–0.3**.

**Watch out for `Power`.** 500 W at 5 m gives a mid grey, and 1000 W already
reaches the maximum the engine can store. Above that threshold more power stops
changing anything — `Reach` is the only remaining lever.

## Exporting

`File > Export > Aliens vs Predator RIF`. Three modes:

| Mode | What it writes |
|---|---|
| **Single Shape** | one active mesh |
| **Multi-Object** | every selected mesh, with no environment |
| **Level Skeleton** | selected meshes plus the level environment (textures, lights, spawns) |

`Level Skeleton` is the default, and the one you want for a level. The settings
are grouped into two sections:

**Level**

| Field | Notes |
|---|---|
| **Mode** | `Level Skeleton` writes the environment; the other two write geometry only |
| **Level Name** | written into the file and shown in the game's level list, so keep it matching your exported file name. Defaults to `custom`. Shown only in Level mode |

**Advanced** — rarely needed:

| Field | Notes |
|---|---|
| **Global Scale** | multiplies every position in the level when the game loads it. Leave at `1.0` |
| **Use Flags Override** | testing only; leave off |
| **ShapeFlag_PreLit Override** | testing only; leave off |

Everything else is taken from the scene: textures from the materials, lights
from the light objects, spawns and generators from the objects you placed with
`Add > AvP`, and the spawning budget from a Global Generator Data marker.

The exporter warns rather than failing when something is missing, so watch the
status line after an export. It reports a level with no player start, a scene
with no World to supply ambient light, and any AvP object it could not export.

### About Global Scale

This setting writes the `ENVSDSCL` chunk, a **level-wide scale multiplier**: the
loader does `local_scale = GlobalScale * ENVSDSCL.scale`, so it scales geometry
placement, generators, waypoints and lights together.

At `1.0` **no chunk is written at all**, which is what 58 of the 63 retail levels
do — multiplying by one is what the chunk's absence already means. Retail uses
`0.8` and `0.9` in the five levels that scale.

It is useful for fitting a level that was built at the wrong size onto the
game's expectations without rebuilding the geometry. It is not a substitute for
building at the right scale: the engine's own positions, speeds and ranges are
not scaled with it.

Textures come **from the materials** — there is no `Texture Override` field any
more. Each material carries its own in-game texture name, so different walls can
have different textures with no extra setup.

## Getting the level into the game

Put the exported file in `<game directory>\avp_rifs\custom\`.

**The file name decides which list the level appears on.** A custom level shows
up under coop and skirmish only if its name contains a **lowercase `(c)`** —
for example `MyMap(c).rif`. Without it the level is treated as a deathmatch map.
The check is case-sensitive, so `(C)` and `(Custom)` do not count.

## Before you launch the game

Check the file with the validator — it catches the mistakes that show up in the
game as an empty level with no message at all:

```
python tools/validate_level.py --game "<game directory>" <exported>.rif
```

Supplying the game directory (`--game`, or the `AVP_GAME_DIR` environment
variable) enables **the most important check**: the validator compares every
texture name in `BMPNAMES` against the game's real archives and says plainly
when one is missing.

That matters because **an unresolved texture does not merely lose its image** —
the engine turns that wall into an unlit one and stops drawing it. A level where
every texture is unresolved looks like empty space even though collisions work.
Look for the line:

```
note    all 15/15 BMPNAMES names exist in the game's archives
```

or the warning `... are not in the game's archives`, which names the culprit.

## Common problems

| Symptom | Cause | What to do |
|---|---|---|
| **The level is empty** | a material with no game texture (its name is not in the archives) | run **Load Game Textures** and use materials from the library |
| **Walls are black and take no light** | an unresolved texture — the engine degrades the wall to unlit | as above; this is not a lack of lights |
| **Everything is uniformly bright** | `World > Strength` too high | bring it down to 0.05–0.3 |
| **Everything is black** | no lights and zero ambient | add a lamp or raise `Power` |
| **Power changes nothing** | the light is at the brightness ceiling | shorten `Reach` |
| **The RIF Light panel is missing** | the selected object is not a Point/Spot light | select a lamp; `Object > Debug > Select RIF Lights` |
| **The reach is not drawn** | `Reach = 0`, or `Show Reach in Viewport` is off | set `Reach` and enable the preview |
| **No enemies appear** | no NPC generator in the level, or generators only of a single-player kind | add an **Alien Drone**, **Predalien** or **Praetorian** generator |
| **Load Game Textures does nothing** | wrong `Game Directory` | point at the folder containing the `fastfile` subfolder |
| **A material is named `...something.RIM`** | the name is longer than 63 characters (Blender's limit) | the full name is in the `RIM_PATH` property and export uses that |
| **One texture did not import** | menu artwork with 11 planes — unsupported | skipped deliberately; not needed for levels |
| **The tests in Blender show old errors** | the copy inside the `.blend` file is stale | run `tests/run_tests_from_disk.py` (see below) |

### Tests inside Blender

Run the suite headlessly — the quickest way, and the only one suitable for a
build script, because the process exit code reports failure:

```
blender --background --factory-startup --python tests/run_tests_from_disk.py
```

From inside Blender, open `tests/run_tests_from_disk.py` in the Text Editor and
press **Run Script** (Alt+P). The script loads the module straight from the
file, prints the result, and writes a log next to it.

The tests exist in two places: on disk
(`tests/blender_roundtrip.py`) and as a **copy inside the
`.blend` file**. The copy is a snapshot — fixes on disk do not change it, so
running the copy reports errors that have already been fixed. Running the script
above always measures the code on disk, and prints each loaded module with its
modification time so a stale copy is visible at a glance.
