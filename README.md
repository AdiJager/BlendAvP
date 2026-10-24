# Aliens vs Predator RIF Exporter

A Blender extension that writes `.rif` levels for **Aliens versus Predator
(1999)**, the original Rebellion game and its *Classic* re-release.

---

## 1. About

### What it does

Build a level in Blender out of ordinary meshes, place the gameplay objects from
`Add > AvP`, and export a `.rif` the game loads. No external tool sits in the
middle and nothing needs converting.

| | |
|---|---|
| **Geometry** | any mesh becomes a level module, with collision |
| **Textures** | read straight out of the game's own archives |
| **Geometry import** | `Load Shapes` reads the game's own 322 model files into a scene, so a level can use retail's furniture, screens and fittings |
| **Lighting** | Blender point and spot lamps, plus ambient from the World |
| **Placed lights** | a lamp carries a `PLOBJLIT` record, a corona on its polygons, and goes dark when shot |
| **Player starts** | single player and multiplayer, one per player slot |
| **NPC generators** | eleven kinds, with a spawning budget |
| **Pickups** | weapons, ammunition, medkit, armour, energy orb, security pass — each with its **real model**, read from the game on demand |
| **Scenery** | static dressing, furniture, video screens, light fittings |
| **Destructibles** | an object breaks into pieces and can notify a target when it does |
| **Moving geometry** | doors (morph modules), platform lifts, fans and levers, each with a track |
| **Switches** | levers operated with the game's own operate key, and area switches that fire on entry |
| **Sound** | emitters with an inner and outer range, auditioned in Blender |
| **Navigation** | alien waypoints and patrol paths, with the link graph worked out for you |
| **Particle effects** | emitters with a rate, a lifetime and a shape |
| **Single-player logic** | death volumes, mission objectives and messages, self destruct, area and multi switches |
| **Module linking** | by Outliner, automatically by adjacency, or from a list per module |

### What it does not do

Listed plainly, because a missing feature that behaves silently is worse than
one that announces itself.

| | |
|---|---|
| **No level import** | a whole retail level cannot be opened in Blender. Individual **models** can (`Load Shapes`), which is the part that was missing for pickups and scenery |
| **No custom textures** | only the textures already in the game can be used. Adding new ones means writing to the game's own archives, which the game does not take kindly to |
| **Some sound kinds** | indexed sounds (`INDSOUND`) and sounds attached to moving objects (`TRAKSOUN`) are not written |

### What is planned

- **Level import**, which needs a Huffman decompressor for the compressed
  `REBCRIF1` files retail ships its levels in
- **`SHPFRGTP`**, the named fragment types the game uses for glass and lights
- **Custom textures**, which means writing to the game's own archives
  value on a placed object
- **Weights for the remaining generator kinds**, for single-player levels

---

## 2. Installation

### Requirements

- **Blender 4.2 or newer.** Developed against 5.2 LTS.
- **Aliens versus Predator Classic** installed, for its textures and sounds.

### Installing the extension

1. Download `rif_exporter-<version>.zip`.
2. In Blender: **`Edit > Preferences > Add-ons`**, then the dropdown in the
   top-right and **`Install from Disk…`**.
3. Choose the zip. The add-on appears as **Aliens vs Predator RIF Exporter**; tick it.

It installs into Blender's own extensions folder, and updates or removes the
same way.

### Building it yourself

```
blender --command extension build --source-dir avp_rif_exporter
```

or use the bundled helper, which also validates the result:

```
python avp_rif_exporter/tools/build_extension.py
```

### Pointing at the game

Texture and sound loading both read from your game install, so tell the add-on
where it is. In **`Edit > Preferences > Add-ons > Aliens vs Predator RIF Exporter`**, set **Game
Directory** to the folder containing `avp_rifs` and `fastfile`.

### The settings in full

Everything lives in **`Edit > Preferences > Add-ons > Aliens vs Predator RIF Exporter`**.

| | |
|---|---|
| **Game Directory** | the folder holding `avp_rifs`, `shape_rifs` and `fastfile` |
| **FFL's Unpack Directory** | where the textures unpack, and where the import reads them back from — one setting, so the two cannot disagree |
| **Pack Images Into .blend** | store the converted textures inside the .blend. On, the file is self-contained but larger; off, it references the `.png` files under the game directory and only opens on a machine that has them |
| **Load Game Textures** | the main button: unpacks the archives, converts them and builds the blender material library |
| **Load Shapes** | reads the game's 322 model files into a scene of their own, with UVs and materials |
| **Build Texture Gallery** | makes a scene showing every imported texture, for finding one by eye |
| **Clear Texture Library** | removes the imported materials, for starting over |

Below them the panel reports **Materials in library**, so you can see whether
the import has happened.

**Nothing else needs setting up.** With the game directory wrong or unset the
exporter still writes geometry — you simply get no textures and no sound list.

### Where the unpacked textures go

`FFL's Unpack Directory` decides where the texture archives are unpacked **and**
where the texture import reads them back from, so the two cannot disagree.

| Setting | Where |
|---|---|
| **FFL Dir** (default) | `<game>/fastfile/_ffl_unpacked` |
| **Game Dir** | the folder named in **Game Directory**, as it is |
| **Custom Dir** | the folder you pick, as it is |

Use **Custom Dir** when the game folder is read-only or shared. An empty Custom
Dir falls back to the default rather than unpacking into a relative path.

### Loading the game's models

`Load Shapes` reads `shape_rifs` — 322 files, 1 234 models — into a Blender scene
called `shape_rifs`, one object per shape, with UVs, smooth shading and a
material named after its texture.

It is not required for anything: pickups read their own model on demand. It is
there for building a level out of retail's own furniture, screens and fittings.

Textures are **not** created by it. A shape knows the **path** of a `.RIM`, not
the pixels, so run `Load Game Textures` first if the models should be textured;
otherwise the material carries only the path.

### What the import costs

**Load Game Textures is slow, and that is expected.** The game keeps 1028
textures inside 58 compressed archives; the add-on unpacks every one, converts
it to `.png` and builds a material for it. **On a first run this takes several
minutes**, and the progress bar moves in fits because each archive is a single
step. Let it finish — interrupting part-way leaves a partial library, and
**Clear Texture Library** then starts you clean.

**Blender will also stutter, and may freeze for a few seconds at a time.** That
is shader compilation: each new material has to be compiled before it can be
drawn, and the first time you look at a texture, or scroll through the gallery,
Blender does that work on the spot. **It settles as you use the library** — the
second pass over the same materials is smooth. It is not a sign that anything is
wrong, and nothing is lost by waiting.

---

## 3. The `Add > AvP` menu

Every gameplay object is placed from `Add > AvP`. The categories are listed
alphabetically, and `Launch Game` sits at the bottom.

Many entries do more than drop a marker: the **Objects** and **Modules** presets
build geometry, and a pickup arrives as the model the game itself uses.

### Effects — 1 entry

- **Particle Generator** — an emitter with a rate, a lifetime and a shape. The
  rate is the engine's own unit: a bigger number means more particles, and zero
  is treated as one a second.

### Gameplay — 8 entries

The level's logic: **Global Generator Data**, **Death Volume**, **Mission
Objective**, **Mission Message**, **Self Destruct**, **Area Switch**, **Multi
Switch** and **End Point**.

- **Global Generator Data** holds the spawning budget — the default enemy limit,
  spawns per minute and the hive state change time. One per level; a second is
  a mistake.
- **Area Switch** fires when the player walks into its volume. **Multi Switch**
  sends different requests to several targets.
- **End Point** is an empty marking where a track finishes. It never reaches the
  level; it is read by whatever object the track belongs to.

### Modules — 2 entries

Shell geometry, loaded down the module path so it reaches the visibility and AI
graphs.

- **Room** — a box with its faces pointing **inward**, so you stand inside it.
  Three materials: `Floor`, `Walls`, `Ceiling`, all with backface culling on.
  Scale it to the size of the space.
- **Door** — a module with its door flags on. It **morphs** open rather than
  sliding, so it deforms in place: give it the thickness of the wall it sits in
  and shape it as the doorway.

### Navigation — 2 entries

- **Alien Waypoint** — a navigation volume for the aliens.
- **Patrol Path** — a curve whose control points are the route.

### NPC Generator — 11 entries

Nine fixed creatures (**Alien Drone, Predalien, Praetorian, Predator,
Facehugger, Xenoborg, Queen, Marine, Sentry Turret**), plus **Default Enemy
Generator** and **Mixed Enemy Generator**.

The two generic ones draw on the level's spawning budget, which lives in
**Global Generator Data**. A generator only produces enemies while its own module
is **not** visible to the player, which is what makes the level's module layout
matter.

### Objects — 6 entries

Each one builds a mesh with the **`AvP Object`** switches already set, so the
work starts from something that behaves:

| Entry | Preset |
|---|---|
| **Simple Object** | nothing on: plain decoration, the commonest placed object in retail |
| **Platform Lift** | platform lift + track |
| **Fan** | fan + track |
| **Lever** | lever + track, with the Switch section ready for a target |
| **Lamp** | light + fragments + destructible, with two pieces |
| **Destructible** | fragments + destructible, with three pieces |

The two fragmenting presets come with their pieces as **children**, because
children are what the exporter reads as the pieces.

### Pickups — 20 entries

Eight marine weapons, seven kinds of ammunition, the **Medkit**, **Armour**,
**Energy Orb** and **Security Pass**.

Each entry reads its model from the game's `shape_rifs` folder on demand, so a
pickup arrives as the object retail uses rather than a cube. If the model cannot
be found the entry falls back to a cube and **says why**.

Only the marine's weapons exist as pickups: the Predator and the alien begin
armed, so no retail level places one for them.

### Player Start — 2 entries

- **Single Player** — where the player begins. One per level.
- **Multiplayer Spawn** — one per player slot, with the races it accepts.

A level wants one or the other. A level with no start still loads, but drops the
player at the world origin.

The mission around the player is written with the **Gameplay** entries: death
volumes, mission objectives, messages, self destruct and the switches. A level
with a single-player start and those is a single-player level.

### Sound — 1 entry

One emitter, drawn as a wireframe sphere whose **size is the range**. Scale the
sphere and the reach follows.

- **Inner** is the part of that sphere at full volume, as a fraction.
- **Sound** is the file, narrowed to the level's folder, most-used first.
- **Audition Sound** plays it without leaving Blender.

The folder itself is set once per level, in **`Properties > World > Level
Audio`**.

---

## 3a. The `AvP Object` panel

Every mesh has this panel, on `Properties > Object`. The **Type** at the top
decides which sections appear:

**Module** — appearance, AI navigation, door flags, and the **Links** list.

**Simple Object** — everything a placed object can be:

| Section | What it sets |
|---|---|
| **Pickup** | what the object **is**: kind and subtype, from the game's own `INANIMATEOBJECT_TYPE` |
| **Trigger** | what happens **to** it: destructible, how many hits, the explosion, and a target to notify when it breaks or is picked up |
| **Fragments** | that its children are the pieces it breaks into |
| **Platform lift**, **Fan**, **Light**, **Switch** | the four behaviours, each with its own settings |
| **Movement** | the track: end point, seconds, loop, smoothing |

Plugin and Movement are separate on purpose: an object can be a lift *and*
notify a target when destroyed.

---

## 3b. The export panel

`File > Export > AvP RIF` opens a panel with three decisions:

**Mode** — `Level Skeleton` writes the environment around the meshes,
`Collection` and `Shape` write only what is selected.

**Link Mode** — where the module tree comes from. The tree becomes `OBJCHIER` and
also feeds the visibility graph, so it decides what the engine thinks can see
what.

| Mode | The tree |
|---|---|
| **By Outliner** (default) | a module links to its parent and children, as arranged in the Outliner |
| **Auto** | each module links to the nearest one it **touches**, worked out from the bounding boxes. 99.3% of retail's 16 148 modules touch a neighbour, so this works without parenting anything |
| **By List** | each module links to the modules named in its own **Links** list |

**Custom Name** — off by default, and then the level is named after the **scene**.
A `.blend` holds several scenes, so one file can hold several levels without
renaming anything. Turn it on to write a name of your own into `RIFFNAME` — the
name the game shows in its level list.

## 4. Quick start

From an empty scene to a level the game runs.

**1. Build the geometry.** Ordinary meshes — a cube is a room. Everything the
player can walk through must be inside one.

**2. Set the World.** `Properties > World` gives the ambient colour. In the
**Level Audio** panel there, pick a **Sound Directory** if the level will have
sound.

**3. Place the gameplay.** From `Add > AvP`:

- one **Player Start**
- a **Global Generator Data**, if the level spawns enemies
- one or more **NPC Generators**
- any **Pickups**, **Objects** and **Sound** emitters

**3a. Link the modules.** Two rules, and a level misbehaves without either.

- **Every module's pivot must sit at `0.0.0`.** The exporter writes each
  module's position and its geometry separately, and reads the position from the
  object's origin; a pivot anywhere else moves the geometry away from the
  collision that is supposed to match it.
  `Object > Set Origin > Origin to World Origin` fixes an object that has
  drifted.
- **Link modules by parenting them.** Select the child first, then the parent,
  and press **`Ctrl+P`** — or drag one object onto another in the Outliner.
  This is the visibility graph: a module sees its parent and its children, and
  nothing else. A module with neither sees only itself, which is how a room is
  kept hidden until the player opens the door. Parent a room to the corridor it
  leads off.

**4. Point at the game.** `Edit > Preferences > Add-ons > Aliens vs Predator RIF Exporter > Game
Directory`, then **Load Game Textures** to bring the game's textures in.

**5. Texture the walls.** Assign the imported materials as you would any other.

**6. Check before exporting.** **`Object > Debug > Validate Level`** reports
what the engine will not tolerate. Fixing those here beats deducing them from a
level that half-works.

**7. Export.** `File > Export > AvP RIF Level`.

**8. Install it.** Put the `.rif` in the game's **`avp_rifs\custom`** folder.
That folder is what the game scans, and a file anywhere else is never seen.

### The `(c)` in the filename

A **coop** level is recognised by a lowercase **`(c)`** before the extension —
`mylevel(c).rif`. Add it for a coop map. A **skirmish** map is found by being in
`avp_rifs\custom` alone and needs no marker.

### About Global Scale

Under *Advanced* in the export panel. It rescales the whole level on the way
out; leave it at **1.0** unless you are matching an existing map's proportions.

---

## 5. Common mistakes

Each of these produces a level that loads and then misbehaves, which is why they
are worth reading before the first export rather than after.

| What you see | What it is |
|---|---|
| **The level loads but nothing spawns** | A generator only works while its module is hidden from the player. With a single module, or a layout where the generator's room is always in view, it never fires |
| **Nothing spawns, and the budget looks fine** | `NPCs per Minute` set to **0** in Global Generator Data switches the whole generator system off, silently. Three retail levels do this on purpose |
| **The game crashes on load** | Two modules overlap. The engine assumes level space is divided, not shared, and overlapping volumes break it. Run **`Object > Debug > Validate Level`**, which reports every overlapping pair |
| **Geometry sits away from where it should collide** | The module's pivot is not at `0.0.0`. The position is read from the object's origin, so a moved pivot moves the collision with it |
| **A room is visible from across the level** | It is not parented into the visibility graph, and a module with no parent and no children sees only itself — which is right for a sealed room and wrong for an open one |
| **The player falls through the floor** | The mesh is not inside a module, or the level has one module and it is not where the player stands |
| **A pickup does nothing when touched** | It may have fallen back to a cube because its model was not found — the console says which file. Check **Game Directory** points at the folder holding `shape_rifs` |
| **Everything is a plain box** | The game's models are not being read at all. Set **Game Directory** and import the shape library to see what is missing |
| **Sounds are silent** | **Sound Directory** is unset in `Properties > World > Level Audio`. Audio is addressed as folder plus file name, so without a folder none of it resolves |
| **A sound is audible only at one point** | The emitter's sphere has been scaled to nothing. Its size *is* the range |
| **The level does not appear in the game** | The file is not in `avp_rifs\custom`, which is the only folder the game scans |
| **Walls are untextured** | The materials were never assigned, or the game directory is unset so nothing could be imported |
| **A material is named `…something.RIM`** | Its name is longer than the game can store; shorten it |
| **The light is as bright as it will go** | The engine's brightness ceiling. Past it, more Power changes nothing |

---

## 6. Credits and links

### The game

**Aliens versus Predator** and its *Classic* re-release are the work of
**Rebellion Developments** and **20th Century Fox**. This tool writes files for
that game; it contains none of its assets, and using it requires your own
licensed copy.

### Standing on other people's work

- **The AvP Modding Tools Bible** — the manual for Rebellion's original editor
  suite. It documents the engine's architecture, and reading it first saved days
  of guessing.
- **The AvP modding community**, for keeping a twenty-five-year-old game
  editable.

### This extension

- **AdiJager** — research and testing
- **DeepSeek** — reverse engineering and coding

### Translating the interface

Every translatable string sits in **strings.json**, keyed by its English text.
Fill in a language there and it is registered with py.app.translations the
next time the add-on loads; nothing in the code changes. An empty entry keeps
English, so a half-finished language is usable rather than full of blanks.

pl_PL and en_US are the two that ship the file. Adding another is a matter of
one more key under languages.

### The repository

<https://github.com/AdiJager/BlendAvP>

Issues, corrections and pull requests are welcome there.

### Thanks

To the original developers of **Aliens versus Predator**, whose engine is still
worth writing tools for twenty-five years on.

---

## 7. Licence

**GPL-3.0-or-later.** See `LICENSE`, or
<https://www.gnu.org/licenses/gpl-3.0.html>.

This matches Blender's own licence, which the extension API expects of add-ons
distributed through it.
