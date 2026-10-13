# RIF Exporter

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
| **Lighting** | Blender point and spot lamps, plus ambient from the World |
| **Player starts** | single player and multiplayer, one per player slot |
| **NPC generators** | eleven kinds, with a spawning budget |
| **Pickups** | weapons, ammunition, medkit, armour, energy orb, security pass |
| **Scenery** | static dressing, furniture, video screens, light fittings |
| **Sound** | emitters with an inner and outer range, auditioned in Blender |
| **Navigation** | alien waypoints, with the link graph worked out for you |

### What it does not do

Listed plainly, because a missing feature that behaves silently is worse than
one that announces itself.

| | |
|---|---|
| **No geometry import** | models from the game cannot be brought in yet, so a pickup placed today is a placeholder cube. The pipeline is ready for a real mesh — it just has to be modelled or imported elsewhere first |
| **Placed lights cannot be shot out** | `Objects > Lights` gives the fittings retail hangs in a corridor, but the destructible kind — the lamps that go dark when hit — need the `PLOBJLIT` chunk, which is not written yet. Ordinary lighting is unaffected: it comes from Blender lamps and works |
| **No moving geometry** | doors, lifts, fans and platforms are not supported; the `Morphs` entry is a placeholder |
| **No particle effects** | the `Effects` entry is a placeholder |
| **No custom textures** | only the textures already in the game can be used. Adding new ones means writing to the game's own archives, which the game does not take kindly to |
| **No level import** | a retail `.rif` cannot be opened in Blender |
| **No single-player missions** | a level can place a player start and enemies, but objectives, triggers, mission scripting and NPC patrols do not exist yet. What can be built today is a **coop or skirmish** map — geometry, lighting, enemies, pickups and sound — and a single-player level only as far as walking around it |
| **Some sound kinds** | indexed sounds (`INDSOUND`) and sounds attached to moving objects (`TRAKSOUN`) are not written |

### What is planned

- **Geometry import**, which unblocks models for pickups, scenery and lights
- **`PLOBJLIT`**, so placed lights can be shot out and go dark like retail's
- **Doors, lifts and fans** — twelve retail strategies, one at a time
- **Destroyable objects**, which need little more than a mass and an integrity
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
3. Choose the zip. The add-on appears as **RIF Exporter**; tick it.

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
where it is. In **`Edit > Preferences > Add-ons > RIF Exporter`**, set **Game
Directory** to the folder containing `avp_rifs` and `fastfile`.

### The settings in full

Everything lives in **`Edit > Preferences > Add-ons > RIF Exporter`**.

| | |
|---|---|
| **Game Directory** | the folder holding `avp_rifs` and `fastfile` |
| **Pack Images Into .blend** | store the converted textures inside the .blend. On, the file is self-contained but larger; off, it references the `.png` files under the game directory and only opens on a machine that has them |
| **Load Game Textures** | the main button: unpacks the archives, converts them and builds the blender material library |
| **Build Texture Gallery** | makes a scene showing every imported texture, for finding one by eye |
| **Clear Texture Library** | removes the imported materials, for starting over |

Below them the panel reports **Materials in library**, so you can see whether
the import has happened.

**Nothing else needs setting up.** With the game directory wrong or unset the
exporter still writes geometry — you simply get no textures and no sound list.

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

Every gameplay object is placed from `Add > AvP`, in nine categories.

### Player Start — 2 entries

- **Single Player** — where the player begins. One per level.
- **Multiplayer Spawn** — one per player slot, with the races it accepts.

A level wants one or the other. A level with no start still loads, but drops the
player at the world origin.

**The start marker is all the single-player support there is.** It puts the
player in the level; the mission around them — objectives, triggers, patrols —
is not implemented, so build coop or skirmish maps for now.

### NPC Generator — 11 entries

Nine fixed creatures (**Alien Drone, Predalien, Praetorian, Predator,
Facehugger, Xenoborg, Queen, Marine, Sentry Turret**), plus **Default Enemy
Generator** and **Mixed Enemy Generator**.

The two generic ones draw on the level's spawning budget, which lives in
**Global Generator Data** — see *Gameplay* below. A generator only produces
enemies while its own module is **not** visible to the player, which is what
makes the level's module layout matter.

### Pickups — 20 entries

Eight marine weapons, seven kinds of ammunition, the **Medkit**, **Armour**,
**Energy Orb** and **Security Pass**.

Only the marine's weapons exist as pickups: the Predator and the alien begin
armed, so no retail level places one for them.

### Objects — 4 entries

Scenery, in the four kinds the game distinguishes: **Static** (tanks, grills),
**Furniture** (crates, drums, seats), **Screens** and **Lights**.

`Lights` places the fittings themselves. The destructible behaviour — a lamp
that goes dark when shot — needs a chunk that is not written yet, so these are
currently ordinary scenery. Lighting a room is a separate thing, and comes from
Blender lamps.

### Sound — 1 entry

One emitter, drawn as a wireframe sphere whose **size is the range**. Scale the
sphere and the reach follows.

- **Inner** is the part of that sphere at full volume, as a fraction.
- **Sound** is the file, narrowed to the level's folder, most-used first.
- **Audition Sound** plays it without leaving Blender.

The folder itself is set once per level, in **`Properties > World > Level
Audio`**.

### Effects and Morphs — 1 entry each

**Placeholders.** They exist so the shape of the tool is visible and so nothing
is quietly missing. Placing one writes nothing, and the export says so.

### Navigation — 2 entries

- **Alien Waypoint** — a navigation volume for the aliens.
- **Patrol Path** — planned; not written yet.

### Gameplay — 1 entry

**Global Generator Data** — the level's spawning budget: the default enemy, the
NPC limit, spawns per minute and the hive state change time. **One per level**;
a second is a mistake.

---

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

**4. Point at the game.** `Edit > Preferences > Add-ons > RIF Exporter > Game
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
| **A pickup does nothing when touched** | Check it is not the placeholder cube buried inside a wall |
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
