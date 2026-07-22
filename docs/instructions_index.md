# Desynced Behavior Instruction Index

Auto-generated reference of every entry in `data.instructions` (`data/instructions.lua`), for writing new behaviors in the visual programming editor. Regenerate by re-parsing that file if it changes.

**Total instructions:** 196 (excludes the internal `nop` placeholder used for deleted instructions, and commented-out/dead definitions in the source. Four entries — `dodock`, `doundock`, `domovexy`, `getxy` — were found missing from an earlier generation of this index and added by hand 2026-07-19; two of them carry malformed `args` metadata in the game source itself, noted inline on their entries)

## How to read this

Each instruction is listed as:

```
### Name (`id`)
Description
- in/out/exec  Label — arg description [filter] (extra param)
```

- **in** — input value consumed by the instruction (register, literal, or parameter)
- **out** — output value the instruction writes to a register
- **exec** — an execution branch (a wire out of the node to the next instruction(s)); instructions with more than one `exec` arg branch based on a condition; instructions with zero `exec` args fall through to the next instruction in sequence
- **(extra param)** — this arg slot is a UI-only literal expander (`expanded=true` in source), typically shown as an additional inline field rather than a pluggable wire
- **(loop)** — instruction implements `next`/`last` and behaves as a loop/iterator (e.g. "for each") rather than running once
- **(hidden literal)** — instruction has a `make_asm`/custom `node_ui` and takes a configured value (dropdown, text field, sub-behavior picker, etc.) baked into the instruction node itself, not listed as an `args` entry

### Filter legend (input value-type restrictions)

- `any` — Any value, including negative numbers
- `data` — Any data value (no numbers)
- `entity` — Entity register only (no literal selection in UI)
- `posnum` — Positive number only
- `num` — Number (may allow negative/infinite/NOT depending on field)
- `coord` — Coordinate value
- `coord_num` — Number or coordinate
- `item` — Item type only
- `item_num` — Item type or number
- `comp` — Component item only
- `comp_num` — Component item or number
- `frame` — Frame (unit/building) type only
- `frame_num` — Frame type or number
- `frame_item` — Frame or item type
- `radar` — Broad filter: number, item, frame, or entity-filter tag
- `resource_num` — Resource tag or number
- `tech` — Technology entry
- `Space` — Space/reserved slot in argument list (no value)

## Categories

- [Flow](#flow) (43)
- [Unit](#unit) (57)
- [Move](#move) (17)
- [Component](#component) (7)
- [AutoBase](#autobase) (8)
- [Global](#global) (26)
- [Math](#math) (32)
- [Memory](#memory) (6)

## Flow

### *Loop Signal* (`for_signal`) *(loop)* *(deprecated)*

*DEPRECATED* Use Loop Signal (Match) instead

- **in** Signal — Signal
- **out** Unit — Unit with signal
- **exec** Done — Finished looping through all units with signal

### Break (`last`)

Break out of a loop

### Call (`call`) *(hidden literal)*

Call a subroutine

### Clear Research (`clear_research`)

Clears a research from research queue, or entire queue if no tech passed

- **in** Tech — Tech to remove from research queue `[tech]`

### Comment (`cmt`)

Freestanding comment node

### Compare Item (`compare_item`)

Compares Item or Unit type

- **exec** If Different — Where to continue if the types differ
- **in** Value 1
- **in** Value 2

### Compare Register (`compare_register`)

Compares Registers for equality

- **exec** If Different — Where to continue if the registers differ
- **in** Value 1
- **in** Value 2

### Compare Unit (`compare_entity`)

Compares Units

- **exec** If Different — Where to continue if the units differ
- **in** Unit 1
- **in** Unit 2

### Data type switch (`value_type`)

Switch based on type of value

- **in** Data — Data to test
- **exec** Item — Item Type
- **exec** Unit — Unit Type
- **exec** Component — Component Type
- **exec** Tech — Tech Type *(extra param)*
- **exec** Value — Information Value Type *(extra param)*
- **exec** Coord — Coordinate Value Type *(extra param)*

### Exit (`exit`)

Stops execution of the behavior

### Get Max Stack (`get_max_stack`)

Returns the amount an item can stack to

- **in** Item — Item to count `[item_num]`
- **out** Max Stack — Max Stack

### Get Research (`get_research`)

Returns the first active research tech

- **out** Tech — First active research

### Get Research Requirement (`get_research_requirement`)

Returns the research required (if needed)

- **in** Tech — The research to investigate for prior tech requirements `[tech]`
- **out** Requirement — The tech required for the research (if needed)

### Has Like Component (`has_like_component`)

Checks Unit for a component type

- **in** Component — Component
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*
- **exec** Failed — Failed

### Have Item (`have_item`)

Checks if you have at least a specified amount of an item

- **in** Item — Item to count `[item_num]`
- **exec** Have Item — have the specified item
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*

### Is Empty (`is_empty`)

Checks a value if it is empty

- **in** Value — Value to check
- **exec** Empty — Where to continue if the value is empty
- **exec** Has Value — "Where to continue if the value exists" },
		--{ 'exec' `[Space]` *(extra param)*

### Is Researched (`is_unlocked`)

Checks whether a faction has something researched

- **in** Id — Input Id
- **exec** No Match — Execution path if there is no match

### Is a (`is_a`)

Compares if an item of unit is of a specific type

- **exec** If Different — Where to continue if the units differ
- **in** Item
- **in** Type

### Jump (`jump`)

Jumps execution to label with the same label id

- **in** Label — Label identifier `[any]`

### Label (`label`)

Labels can be jumped to from anywhere in a behavior

- **in** Label — Label identifier `[any]`

### Lock (`lock`)

Run one instruction at a time

### Loop Equipped Components (`for_component`) *(loop)*

Loops through equipped Components

- **out** Component — Equipped component
- **out** Index — Returns the index of the result `[num]` *(extra param)*
- **exec** Done — Finished loop

### Loop Inventory Slots (`for_inventory_item`) *(loop)*

Loops through Inventory

- **out** Inventory — Item Inventory
- **exec** Done — Finished loop
- **out** Reserved Stack — Items reserved for outgoing order or recipe `[num]` *(extra param)*
- **out** Unreserved Stack — Items available `[num]` *(extra param)*
- **out** Reserved Space — Space reserved for an incoming order `[num]` *(extra param)*
- **out** Unreserved Space — Remaining space `[num]` *(extra param)*
- **out** Index — Slot Index `[num]` *(extra param)*
- **in** Unit — Unit `[entity]` *(extra param)*

### Loop Number (`for_number`) *(loop)*

Performs code for all numbers in a range

- **in** From — Loop start number `[num]`
- **in** To — Loop end number `[num]`
- **in** Step — Increment step, use -1 or 1 based on inputs if left empty `[num]` *(extra param)*
- **out** Value — Current number
- **exec** Done — Finished loop

### Loop Producer Items (`for_producers_items`) *(loop, hidden literal)*

Loops through all unlocked items a production component can produce

- **in** Producer — Producer
- **out** Item — Item
- **exec** Done — Finished looping through all items

### Loop Producers (`for_producers`) *(loop)*

Gets all producers for a production with their production time

- **in** Production — Production
- **out** Producer — Producer
- **exec** Done — Finished looping through all item producers

### Loop Recipe Ingredients (`for_recipe_ingredients`) *(loop)*

Loops through Ingredients

- **in** Recipe `[frame_item]`
- **out** Ingredient — Recipe Ingredient
- **exec** Done — Finished loop

### Loop Repair Ingredients (`for_repair_ingredients`) *(loop)*

Loops through each ingredient required to repair a mission unit

- **in** Target — Unit
- **out** Ingredient — Repair Ingredient
- **exec** Done — Finished looping through all mission repair ingredients

### Loop Research (`for_research`) *(loop)*

Performs code for all researchable tech

- **out** Tech — Researchable Tech
- **exec** Done — Finished looping through all researchable tech

### Loop Research Ingredients (`for_research_ingredients`) *(loop)*

Loops through Ingredients

- **in** Research `[tech]`
- **out** Ingredient — Research Ingredient
- **exec** Done — Finished loop

### Loop Research Unlocks (`for_research_unlocks`) *(loop)*

Performs code for all unlocks for a researchable tech

- **in** Tech — Tech `[tech]`
- **out** Unlock — Unlocks
- **exec** Done — Finished looping through all unlocks

### Loop Signal (`for_signal_match`) *(loop, hidden literal)*

Loops through all units with a signal of similar type and additional number checks

- **in** Signal — Signal
- **out** Unit — Found Unit with signal
- **out** Signal — Found signal `[entity]` *(extra param)*
- **exec** Done — Finished looping through all units with signal

### Loop Units (Range) (`for_entities_in_range`) *(loop)*

Performs code for all units in visibility range of the unit

- **in** Range — Range (up to units visibility range) `[num]`
- **in** Filter — Filter to check `[radar]`
- **in** Filter — Second Filter `[radar]` *(extra param)*
- **in** Filter — Third Filter `[radar]` *(extra param)*
- **out** Unit — Current Unit in loop
- **exec** Done — Finished looping through all units in range

### Loop Unlocked Components (`get_unlocked_components`) *(loop)*

Loops through all produceable unlocked components with a recipe

- **out** Item — Item
- **exec** Done — Finished looping through all produceable components

### Parameter Event (`event_parameter`) *(hidden literal)*

Run event when the value of the specified parameter changes

### Radio Event (`event_radio`) *(hidden literal)*

Run event when the signal of the specified radio band changes its value

- **out** Signal — Signal value

### Restart (`restart`)

Restart execution of the behavior

### Select Nearest (`select_nearest`)

Branches based on which unit is closer, optional branches for closer unit

- **exec** A — A is nearer (or equal)
- **exec** B — B is nearer
- **in** Unit A `[entity]`
- **in** Unit B `[entity]`
- **out** Closest — Closest unit *(extra param)*

### Sequence (`sequence`) *(loop)*

Executes a series of exec nodes in sequence

- **exec** First — First
- **exec** Second — Second *(extra param)*
- **exec** Third — Third *(extra param)*
- **exec** Fourth — Fourth *(extra param)*
- **exec** Last — Last

### Set Research (`set_research`)

Add a new research into the active research queue

- **in** Tech — First active research `[tech]`

### Unit Type (`unit_type`)

Divert program depending on unit type

- **in** Unit — The unit to check `[entity]`
- **exec** Building — Where to continue if the unit is a building
- **exec** Bot — Where to continue if the unit is a bot
- **exec** Construction — Where to continue if the unit is a construction site *(extra param)*

### Unlock (`unlock`)

Run as many instructions as possible. Use wait instructions to throttle execution.

### Wait Ticks (`wait`)

Pauses execution of the behavior until 1 or more ticks later

- **in** Time — Number of ticks to wait `[posnum]`

## Unit

### Can Equip (`can_equip`)

Checks if a component can be equipped on a unit

- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*
- **in** Component — Component to equip `[comp]`
- **exec** Cannot Equip — If the component is unable to be equipped

### Check Altitude (`check_altitude`)

Divert program depending on location of a unit or coordinate

- **in** Target — The unit or coordinate to check for (if not self) `[coord]` *(extra param)*
- **exec** Valley — Where to continue if the unit or coordinate is in a valley
- **exec** Plateau — "Where to continue if the unit or coordinate is on a plateau" },
		--{ 'exec' `[Space]` *(extra param)*

### Check Battery (`check_battery`)

Checks the Battery level of a unit

- **exec** Full — Where to continue if battery power is fully recharged
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*

### Check Blightness (`check_blightness`)

Divert program depending on location of a unit or coordinate

- **in** Target — The unit or coordinate to check for (if not self) `[coord]` *(extra param)*
- **exec** Blight — Where to continue if the unit is in the blight

### Check Grid Efficiency (`check_grid_effeciency`)

Checks the Efficiency of the logistics network the unit is on

- **exec** Full — Where to continue if at full efficiency
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*

### Check Health (`check_health`)

Check a unit's health

- **exec** Full — Where to continue if at full health
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*

### Clear All Links (`clear_all_links`)

Clear all register links on this unit

### Clear Link (`clear_link`)

Clear register link

- **in** From — Component/Register Index to start clearing a link `[comp_num]`
- **in** Component Index — Index for when multiple components equipped of same type `[posnum]` *(extra param)*
- **in** To — Component/Register Index to end clearing a link `[comp_num]`
- **in** Component Index — Index for when multiple components equipped of same type `[posnum]` *(extra param)*

### Connect (`connect`)

Connects the Unit to the Logistics Network

### Count Items (`count_item`) *(hidden literal)*

Counts the number of the passed item contained in the unit's inventory

- **in** Item — Item to count `[item]`
- **out** Result — Number of this item in inventory or empty if none exist
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*

### Count Slots (`count_slots`) *(hidden literal)*

Returns the number of slots in this unit of the given type

- **out** Result — Number of slots of this type
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*

### Disable Transport Route (`disable_transport_route`)

Disable Unit to deliver on transport route

### Disconnect (`disconnect`)

Disconnects the Unit from the Logistics Network

### Dock (`dodock`)

Docks an item on the following target

- **in** Target — Unit

### Drop Off Items (`dodrop`) *(hidden literal)*

Drop off items at a unit or location

- **in** Destination — Unit or destination to bring items to `[entity]`
- **in** Item / Amount — Item and amount to drop off `[item_num]` *(extra param)*
- **exec** Path Blocked — If path to destination was blocked

### Enable Transport Route (`enable_transport_route`)

Enable Unit to deliver on transport route

### Equip Component (`equip_component`)

Equips a component if it exists

- **exec** No Component — If you don't current hold the requested component
- **in** Component — Component to equip `[comp]`
- **in** Slot index — Individual slot to equip component from `[posnum]` *(extra param)*

### First Item (`get_inventory_item`)

Reads the first item in the inventory of the unit

- **out** Item
- **exec** No Items — No items in inventory

### Get Active Order (`get_active_order`)

Gets the source, target, and amount data from the current active order

- **out** Source
- **out** Target
- **out** Amount

### Get Closest Unit (`get_closest_entity`)

Gets the closest visible unit matching a filter

- **in** Filter — Filter to check `[radar]`
- **in** Filter — Second Filter `[radar]` *(extra param)*
- **in** Filter — Third Filter `[radar]` *(extra param)*
- **out** Output — Unit

### Get First Locked Id (`get_first_locked_0`)

Gets the first item where the locked slot exists but there is no item in it

- **out** Item — The first locked item id with no item

### Get Inventory Item (`get_inventory_item_index`)

Reads the item contained in the specified slot index

- **in** Index — Slot index `[posnum]`
- **out** Item
- **exec** No Item — Item not found

### Get Item Info (`get_item_info`) *(hidden literal)*

Gets information on an item

- **in** Item — The item to check
- **out** Result — Number of this item's chosen information

### Get Unit Info (`get_unit_info`) *(hidden literal)*

Gets information on a unit

- **in** Unit — The unit to check
- **out** Result — Returns a specific Unit info

### Get Unit Power Info (`get_unit_power_info`) *(hidden literal)*

Gets power information on a unit

- **in** Unit — The unit to check
- **out** Result — Returns a specific Unit's power info

### Get Unit Type (`get_unit_type`)

Get the frame type of the unit

- **in** Unit — The unit to check
- **out** Type

### Get from Component Remotely (`get_reg_remotely`)

Reads a value from a component register on an external unit

- **in** Unit — The unit to get component register from (if not self) `[entity]`
- **in** From — Component and register number to get remotely `[comp_num]`
- **out** Value — Value of Register
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*
- **exec** Failed — Failed to get register *(extra param)*

### Inventory Total (`get_inventory_total`)

Returns the total contained in inventory

- **out** Result
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*

### Is Docked (`is_docked`)

Check if a unit is docked and get its garage

- **exec** No Dock — Where to continue if unit is not docked
- **out** Garage — Unit

### Is Equipped (`is_equipped`)

Check if a specific component has been equipped

- **in** Component — Component to check `[comp]`
- **exec** Component Equipped — Where to continue if component is equipped
- **out** Result — Returns how many instances of a component equipped on this Unit *(extra param)*

### Is Inside Logistics Network (`is_logistics`)

Checks if a unit or coordinates is in the logistics network

- **in** Unit — Unit or Coordinate `[entity]`
- **exec** Outside — If not inside a logistics network

### Is Item Slot Locked (`is_fixed`)

Check if a specific item slot is locked

- **in** Slot index — Individual slot to check `[posnum]`
- **exec** Is Locked — Where to continue if inventory slot is locked

### Is Moving (`is_moving`)

Checks the movement state of a unit

- **exec** Not Moving — Where to continue if unit is not moving
- **exec** Path Blocked — Where to continue if unit is path blocked
- **exec** No Result — Where to continue if unit is out of visual range
- **in** Unit — The unit to check (if not self) `[entity]` *(extra param)*

### Is Same Grid (`is_same_grid`)

Checks if two units or coordinates are in the same logistics network

- **in** Unit — First Unit or Coordinate `[entity]`
- **in** Unit — Second Unit or Coordinate `[entity]`
- **exec** Different — Different logistics networks

### Is Unit A (`is_unit_a`)

Checks if a unit is a specific frame type

- **in** Unit — The unit to check
- **in** Type
- **exec** Is Not

### Load Behavior (`load_behavior`) *(hidden literal)*

Load and run a behavior on an external unit

- **in** Unit — The unit to load the behavior on (if not self) `[entity]`
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*
- **exec** Failed — Failed

### Lock Item Slots (`lock_slots`) *(hidden literal)*

Lock all storage slots or a specific item slot index

- **in** Item — Item type to try locking to the slots `[item_num]`
- **in** Slot index — Individual slot to lock `[posnum]` *(extra param)*

### Loop Nearby Resources (`for_count_resources`) *(loop)*

Scans for nearby resources in power field or visibility range

- **out** Resource
- **exec** Done

### Match (`match`)

Filters the passed unit

- **in** Unit — Unit to Filter, defaults to Self `[entity]`
- **in** Filter — Filter to check `[radar]`
- **in** Filter — Second Filter `[radar]` *(extra param)*
- **in** Filter — Third Filter `[radar]` *(extra param)*
- **exec** Failed — Did not match filter

### Order Transfer To (`order_transfer`)

Transfers an Item to another Unit

- **in** Target — Target unit `[entity]`
- **in** Item — Item and amount to transfer `[item_num]`

### Order to Shared Storage (`order_to_shared_storage`)

Request Inventory to be sent to nearest shared storage with corresponding locked item slots

### Pick Up Items (`dopickup`) *(hidden literal)*

Picks up items from a unit

- **in** Source — Unit to take items from `[entity]`
- **in** Item / Amount — Item and amount to pick up `[item_num]` *(extra param)*
- **exec** Path Blocked — If path to destination was blocked

### Read Radio (`read_radio`)

Reads the Radio signal on a specified band

- **in** Band — The band to check for
- **out** Result — Value of the radio signal

### Read Signal (`read_signal`)

Reads the Signal register of another unit

- **in** Unit — The owned unit to check for `[entity]`
- **out** Result — Value of units Signal register

### Request Item (`request_item`) *(hidden literal)*

Requests an item if it doesn't exist in the inventory

- **in** Item — Item and amount to order `[item_num]`
- **in** Channel — Optionally request on a specific logistics channel (1-4) `[posnum]` *(extra param)*

### Request Wait (`request_wait`) *(hidden literal)*

Requests up to a specified amount of an item and waits until that amount exists in inventory

- **in** Item — Item and amount to order `[item_num]`
- **in** Channel — Optionally request on a specific logistics channel (1-4) `[posnum]` *(extra param)*

### Set Link (`set_link`)

Set register link

- **in** From — Component and register number to start a new link `[comp_num]`
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*
- **in** To — Component and register number to end a new link `[comp_num]`
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*

### Set Logistics (`set_logistics_options`) *(hidden literal)*

Sets current unit to the specified logistics settings

### Set to Component Remotely (`set_reg_remotely`)

Writes a value into a component register on an external unit

- **in** Unit — The unit to set component register on (if not self) `[entity]`
- **in** Value — Value to set remotely `[any]`
- **in** To — Component and register number to set `[comp_num]`
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*
- **exec** Failed — Failed to set register *(extra param)*

### Solve Explorable (`solve`)

Attempt to solve an explorable

- **in** Target — Explorable to solve `[entity]`
- **out** Missing — Missing repair item, scanner component or Unpowered
- **exec** Failed — Missing item, component, or power to scan

### Sort Storage (`sort_storage`)

Sorts Storage Containers on Unit

### Switch (`switch`)

Filters the passed unit or item

- **in** Input — Unit or item to Filter `[entity]`
- **in** Case 1 — Case 1 `[radar]`
- **exec** 1 — Case 1
- **in** Case 2 — Case 2 `[radar]` *(extra param)*
- **exec** 2 — Case 2 *(extra param)*
- **in** Case 3 — Case 3 `[radar]` *(extra param)*
- **exec** 3 — Case 3 *(extra param)*
- **in** Case 4 — Case 4 `[radar]` *(extra param)*
- **exec** 4 — Case 4 *(extra param)*
- **in** Case 5 — Case 5 `[radar]` *(extra param)*
- **exec** 5 — Case 5 *(extra param)*

### Turn Off (`shutdown`)

Shuts down the power of the Unit

### Turn On (`turnon`)

Turns on the power of the Unit

### Undock (`doundock`)

Undocks an item on the following target

### Unequip Component (`unequip_component`)

Unequips a component if it exists

- **exec** No Component — If you don't current hold the requested component or slot was empty
- **in** Component — Component to unequip `[comp]`
- **in** Slot index — Individual slot to try to unequip component from `[posnum]` *(extra param)*

### Unlock Item Slots (`unlock_slots`)

Unlock all inventory slots or a specific item slot index

- **in** Slot index — Individual slot to unlock `[posnum]` *(extra param)*

## Move

### *Move Unit (Range)* (`domove_range`) *(deprecated)*

*DEPRECATED* Use Move Unit

- **in** Target — Unit to move to, the number specifies the range in which to be in `[entity]`

### Attack Move (`attack_move`)

Moves towards a location stopping to attack any enemies encountered

- **in** Unit — Unit `[entity]` *(extra param)*

### Get Offset (`get_offset`)

Gets current offset from a unit

- **in** Target — Unit/Coord to get offset from `[coord]`
- **out** Offset — Offset from unit `[coord]`

### Get unit Coordinates (`getxy`)

Gets the X and Y coordinate of a Unit

- **out** X — X Coordinate
- **out** Y — Y Coordinate

*(Source quirk: this instruction's `args` metadata is malformed in `data/instructions.lua` — each entry is double-nested, `{ { 'out', "X", ... } }` instead of `{ 'out', "X", ... }` — the list above is the evident intent. Tooling reading `args[i][1]` as a direction string gets a table instead.)*

### Is Passable (`is_passable`)

Checks whether a location is passable

### Move Away (Range) (`moveaway_range`) *(hidden literal)*

Moves out of range of another unit, the number value of the target specifies the range

- **in** Target — Unit to move away from `[entity]`

### Move East (`move_east`) *(deprecated)*

Moves towards a tile East of the current location at the specified distance

- **in** Number — Number of tiles to move East `[posnum]`

### Move North (`move_north`) *(deprecated)*

Moves towards a tile North of the current location at the specified distance

- **in** Number — Number of tiles to move North `[posnum]`

### Move Offset (`move_offset`)

Moves to a specific offset of current location or specified unit

- **in** Offset — Offset to move to `[coord]`
- **in** Unit — Unit to offset from `[entity]` *(extra param)*

### Move South (`move_south`) *(deprecated)*

Moves towards a tile South of the current location at the specified distance

- **in** Number — Number of tiles to move South `[posnum]`

### Move To Coordinate (`domovexy`)

Move to a specific coordinate

- **in** X — X Coordinate `[num]`
- **in** Y — Y Coordinate `[num]`

*(Source quirk: same malformed double-nested `args` metadata as `getxy` — see that entry.)*

### Move Unit (`domove`) *(hidden literal)*

Moves to another unit or within a range of another unit

- **in** Target — Unit to move to, the number specifies the range in which to be in `[entity]`
- **exec** Path Blocked — Where to continue if unit is path blocked
- **in** Unit — Target Unit `[entity]` *(extra param)*

### Move Unit (Async)* (`domove_async`) *(deprecated)*

*DEPRECATED* Use Move Unit

- **in** Target — Unit to move to `[entity]`

### Move West (`move_west`) *(deprecated)*

Moves towards a tile West of the current location at the specified distance

- **in** Number — Number of tiles to move West `[posnum]`

### Scout (`scout`) *(hidden literal)*

Moves in a scouting pattern around the factions home location

### Scout Range (`scout_rand_range`)

Moves in a random direction a specified amount\nOptionally pass a coordinate to give some directionality

- **in** Range — Range to scout `[posnum]`
- **in** Coord — Last Coordinate `[coord]`

### Stop Unit (`stop`)

Stop movement and abort what is currently controlling the unit's movement

## Component

### Deploys held unit (`deploy`)

Deploys the first found held unit at location specified or current location

- **in** Coord — location to deploy

### Equip Component Remotely (`equip_component_remotely`)

Equips a component if it exists in the unit's inventory

- **in** Unit — The unit to equip component on (if not self) `[entity]`
- **exec** Failed — Failed
- **in** Component — Component to equip `[comp]`
- **in** Slot index — Individual slot to equip component from `[posnum]` *(extra param)*

### Mine (`mine`)

Mine a single resource deposit

- **in** Resource — Resource to Mine `[resource_num]`
- **exec** Cannot Mine — Execution path if mining was unable to be performed
- **exec** Full — Execution path if can't fit resource into inventory

### Radar (`scan`)

Scan for the closest unit that matches the filters

- **in** Filter 1 — First filter `[radar]`
- **in** Filter 2 — Second filter `[radar]`
- **in** Filter 3 — Third filter `[radar]`
- **out** Result
- **exec** No Result — Execution path if no results are found

### Set Signpost (`set_signpost`) *(hidden literal)*

Set the signpost to specific text

### Unequip Component Remotely (`unequip_component_remotely`)

Unequips a component if it exists

- **in** Unit — The unit to equip component on (if not self) `[entity]`
- **exec** Failed — Failed
- **in** Component — Component to unequip `[comp]`
- **in** Slot index — Individual slot to try to unequip component from `[posnum]` *(extra param)*

### Wait Component (`wait_component`)

Waits for a component before continuing behavior

- **in** Component — Component to wait for `[comp]`
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*
- **exec** Not Working — Execution path if the component isn't currently working *(extra param)*

## AutoBase

### Build Registered (`build_registered`)

Places a building to be registered

- **in** Coordinate — Target location, or at currently location if not specified `[coord]` *(extra param)*
- **in** Rotation — Building Rotation (0 to 3) (default 0) `[posnum]` *(extra param)*
- **in** Id — Id to register with
- **exec** If Working — Where to continue if the unit started working
- **exec** Construction Failed — Where to continue if construction fails

### Gather Information (`gather_information`)

Collect information for running the auto base controller

- **in** Range — Range of operation `[posnum]`

### Get Registered (`get_registered`)

Get number of registered buildings

- **in** Id — Id to get register of
- **out** Value — Value of registered Unit

### Make Carriers (`make_carrier`)

Construct carrier bots for delivering orders or to use for other tasks

- **in** Carriers — Type and count of carriers to make `[frame_num]`
- **exec** If Working — Where to continue if the unit started working

### Make Miners (`make_miner`)

Construct and equip miner components on available carrier bots

- **in** Resource/Count — Resource type and number of miners to maintain `[item_num]`
- **in** Frame — Unit to create if none are free `[frame]`
- **exec** If Working — Where to continue if the unit started working

### Make Producer (`make_producer`)

Build and maintain dedicated production buildings

- **in** Item/Count — Item type and number of producers to maintain `[item_num]`
- **in** Component — Production component `[comp]`
- **in** Building — Building type to use as producer `[frame]`
- **in** Location — Location offset from self `[coord]`
- **exec** If Working — Where to continue if the unit started working

### Make Turret Bots (`make_turret_bots`)

Construct and equip turret components on available carrier bots

- **in** Number — Number of turret bots to maintain
- **exec** If Working — Where to continue if the unit started working

### Serve Construction (`serve_construction`)

Produce materials needed in construction sites

- **exec** If Working — Where to continue if the unit started working

## Global

### Abort Construction (`abort_construction`)

Abort an owned construction

- **in** Target — Target Construction `[entity]`

### Activate (`activate`)

Activate

- **exec** Failed — Failed

### Can Produce (`can_produce`)

Returns if a unit can produce an item

- **exec** Can Produce — Where to continue if the item can be produced
- **in** Item — Production Item `[item]`
- **in** Component — Optional Component to check (if Component not equipped) `[comp_num]` *(extra param)*

### DebugPrint (`debug_print`)

Debug print to log

- **in** Print Value — Notification Value

### Distance (`get_distance`)

Get the distance between units or coordinates

- **in** Target — Target unit or coordinate `[coord]`
- **out** Distance — Unit and its distance in the numerical part of the value
- **in** Source — The unit or coordinate to measure from (if not self) `[coord]` *(extra param)*

### Faction Item Amount (`faction_item_amount`)

Counts the number of the passed item in your logistics network

- **in** Item — Item to count `[item]`
- **out** Result — Number of this item in your faction
- **exec** None — Execution path when none of this item exists in your faction

### Get Home (`gethome`)

Gets the factions home unit

- **out** Result — Factions home unit

### Get Ingredients (`get_ingredients`) *(deprecated)*

Returns the ingredients required to produce an item

- **in** Product `[item]`
- **out** Out 1 — First Ingredient
- **out** Out 2 — Second Ingredient
- **out** Out 3 — Third Ingredient

### Get Location (`get_location`)

Gets location of a seen unit

- **in** Unit — Unit to get coordinates of `[entity]`
- **out** Coord — Coordinate of unit

### Get Season (`get_season`)

Divert program depending on season

- **exec** Winter — Where to continue if it is winter
- **exec** Spring — Where to continue if it is spring
- **exec** Summer — Where to continue if it is summer
- **exec** Fall — Where to continue if it is fall

### Get Stability (`get_stability`)

Gets the current world stability

- **out** Number — Stability

### Get Trust (`gettrust`)

Gets the trust level of the unit towards you

- **exec** Ally — Target unit considers you an ally
- **exec** Neutral — Target unit considers you neutral
- **exec** Enemy — Target unit considers you an enemy
- **in** Unit — Target Unit `[entity]`

### Get Type (`get_type`)

Gets the type from an item or unit

- **in** Item/Unit
- **out** Type

### Is Day/Night (`is_daynight`)

Divert program depending time of day

- **exec** Day — Where to continue if it is nighttime
- **exec** Night — Where to continue if it is daytime

### Land (`land`)

Tells a satellite that has been launched to land

### Launch (`launch`)

Launches a satellite if executed on an AMAC or a Drop Pod to the planet if executed on the Mothership

### Look At (`lookat`)

Turns the unit to look at a unit or a coordinate

- **in** Target — Target unit or coordinate `[coord]`

### Notify (`notify`) *(hidden literal)*

Triggers a faction notification

- **in** Notify Value — Notification Value
- **in** Timeout — Notification Value `[num]` *(extra param)*

### Percent (`percent_value`)

Gives you the percent that value is of Max Value

- **in** Value — Value to check
- **in** Max Value — Max Value to get percentage of
- **out** Number — Percent

### Ping (`ping`)

Plays the Ping effect and notifies other players playing in the same faction

- **in** Target — Target unit `[entity]`

### Place Construction (`build`)

Places a construction site for a specific structure

- **in** Coordinate — Target location, or at currently location if not specified `[coord]` *(extra param)*
- **in** Rotation — Building Rotation (0 to 3) (default 0) `[posnum]` *(extra param)*
- **exec** Construction Failed — Where to continue if construction fails

### Produce Registered Unit (`produce_registered`)

Sets a production component to produce a blueprint

- **in** Id — Id to register with
- **exec** If Working — Where to continue if the unit started working

### Produce Unit (`produce`)

Sets a production component to produce a blueprint

### Read Key (`readkey`)

Attempts to read the internal key of the unit

- **in** Frame — Structure to read the key for `[entity]`
- **out** Key — Number key of structure

### Remap (`remap_value`)

Remaps a value between two ranges

- **in** Value — Value to Remap
- **in** Input Low — Low value for input
- **in** Input High — High value for input
- **in** Target Low — Low value for target
- **in** Target high — High value for target
- **out** Result — Remapped value

### Resource Type (`get_resource_item`)

Gets the resource type from a resource deposit

- **in** Resource Deposit — Resource Deposit `[entity]`
- **out** Resource — Resource Type
- **exec** Not Resource — Continue here if it wasn't a resource deposit

## Math

### Add (`add`)

Adds a number or coordinate to another number or coordinate

- **in** To `[any]`
- **in** Num `[coord_num]`
- **out** Result

### Bitwise Op (`bitwise_op`) *(hidden literal)*

Performs a bitwise operation on two values

- **in** A — First value (or value for NOT) `[num]`
- **in** B — Second value (ignored for NOT) `[num]` *(extra param)*
- **out** Result — Bitwise operation result

### Check Bit (`check_bit`)

Checks if a specific bit is set in a number

- **exec** Bit Clear — Execution path if bit is clear
- **in** Value — The number to check `[num]`
- **in** Bit Index — Bit index (1 = least significant) `[num]`

### Check space for item (`checkfreespace`)

Checks if free space is available for an item and amount

- **exec** Can't Fit — Execution if it can't fit the item
- **in** Item — Item and amount to check can fit `[item_num]`
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*

### Combine Coordinate (`combine_coordinate`)

Returns a coordinate made from x and y values

- **in** X `[num]`
- **in** Y `[num]`
- **out** Result

### Combine Register (`combine_register`)

Combine to make a register from separate parameters

- **in** Number `[num]`
- **in** Data `[data]`
- **out** Result
- **in** X `[num]` *(extra param)*
- **in** Y `[num]` *(extra param)*

### Compare Number (`check_number`)

Divert program depending on number of Value and Compare

- **exec** If Larger — Where to continue if Value is larger than Compare
- **exec** If Smaller — Where to continue if Value is smaller than Compare
- **in** Value — The value to check with `[num]`
- **in** Compare — The number to check against `[num]`

### Copy (`set_reg`)

Copy a value to a frame register, parameter or variable

- **in** Value `[any]`
- **out** Target

### Divide (`div`)

Divides a number or coordinate from another number or coordinate

- **in** From `[any]`
- **in** Num `[coord_num]`
- **out** Result

### Get Battery (`get_battery`)

Gets the value of the Battery level as a percent

- **out** Result
- **in** Unit — The unit to check for (if not self) `[entity]` *(extra param)*
- **out** Current — Value of battery remaining *(extra param)*
- **out** Max — Value of maximum battery amount *(extra param)*

### Get Equipped Num (`get_equipped_num`)

Returns how many of a component are equipped

- **in** Component ID — Component to search for `[comp_num]`
- **out** Value
- **in** Unit — The unit to check (if not self) `[entity]` *(extra param)*

### Get Grid Efficiency (`get_grid_effeciency`)

Gets the value of the Grid Efficiency as a percent

- **out** Result

### Get Health (`get_health`)

Gets a unit's health as a percentage, current remaining and max amount

- **in** Unit — Unit to check `[entity]`
- **out** Percent — Percentage of health remaining
- **out** Current — Value of health remaining *(extra param)*
- **out** Max — Value of maximum health *(extra param)*

### Get Resource Num (`get_resource_num`)

Gets the amount of resource

- **in** Resource — Resource Node to check `[entity]`
- **out** Result

### Get Self (`get_self`)

Gets the value of the Unit running the behavior

- **out** Unit Reference
- **out** Component/Index — The component identifier and equipped index of the running behavior controller *(extra param)*

### Get Shield (`get_shield`)

Get a unit's shield as a percentage, current remaining and max amount

- **in** Unit — Unit to check `[entity]`
- **out** Percent — Percentage of shield remaining
- **out** Current — Value of shield remaining *(extra param)*
- **out** Max — Value of maximum shield amount *(extra param)*

### Get Unit At (`get_entity_at`)

Gets the best matching unit at a coordinate

- **in** Coordinate — Coordinate to get Unit from `[coord]`
- **out** Result

### Get from Component (`get_comp_reg`)

Reads a value from a component register

- **in** From — Component and register number to get `[comp_num]`
- **out** Value — Value of Register
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*

### Get space for item (`getfreespace`)

Returns how many of the input item can fit in the inventory

- **in** Item — Item to check can fit `[item]`
- **out** Result — Number of a specific item that can fit on a unit
- **in** Unit — The unit to check (if not self) `[entity]` *(extra param)*

### Is Working (`is_working`)

Checks whether a particular component is currently working

- **exec** Is Not Working — If the requested component is NOT currently working
- **in** Component — Specific component to check or empty to check all components `[comp]`
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*
- **out** Value — Returns the currently working component *(extra param)*

### Modulo (`modulo`)

Get the remainder of a division

- **in** Num `[any]`
- **in** By `[coord_num]`
- **out** Result

### Multiply (`mul`)

Multiplies a number or coordinate from another number or coordinate

- **in** To `[any]`
- **in** Num `[coord_num]`
- **out** Result

### Random Coordinate (`random_coordinate`)

Returns a random coordinate from a location within a specified range

- **in** Coordinate — Entity or Coordinate `[coord]`
- **in** Range — Radius range from coordinate `[posnum]`
- **out** Result

### Random Number (`random_number`)

Returns a random number value between a min and max value

- **in** Min `[num]`
- **in** Max `[num]`
- **out** Result

### Separate Coordinate (`separate_coordinate`)

Split a coordinate into x and y values

- **in** Coordinate `[coord]`
- **out** X
- **out** Y

### Separate Register (`separate_register`)

Split a register into separate parameters

- **in** Register `[any]`
- **out** Number
- **out** Target Reference *(extra param)*
- **out** Identifier *(extra param)*
- **out** X *(extra param)*
- **out** Y *(extra param)*

### Set Data (`set_data`)

Sets the data part of a value (identifier, target reference or coordinate)

- **in** Value
- **in** Data `[data]`
- **out** Result

### Set Number (`set_number`)

Sets the numerical part of a value

- **in** Value
- **in** Number `[num]`
- **out** Result

### Set to Component (`set_comp_reg`)

Writes a value into a component register

- **in** Value — Value to set `[any]`
- **in** To — Component and register number to set `[comp_num]`
- **in** Component/Index — Component and index if multiple are equipped `[comp_num]` *(extra param)*

### Simulation Tick (`simulation_tick`)

Returns the current Simulation Tick

- **out** Tick — Simulation Tick

### Square Root (`sqrt`)

Get the square root of a number

- **in** Num `[num]`
- **out** Result

### Subtract (`sub`)

Subtracts a number or coordinate from another number or coordinate

- **in** From `[any]`
- **in** Num `[coord_num]`
- **out** Result

## Memory

### Loop Memory (`memory_loop`) *(loop)*

Loops through memory array or known array identifiers

- **in** Id — Array identifier or empty to loop through known identifiers
- **out** Value
- **exec** Done — Finished loop
- **out** Index *(extra param)*

### Memory Get (`memory_get`)

Get memory array element

- **in** Index — Array identifier and index
- **out** Value

### Memory Insert (`memory_insert`)

Insert value into memory array

- **in** Index — Array identifier and index
- **in** Value — Value

### Memory Length (`memory_length`)

Get length of memory array

- **in** Index — Array identifier
- **out** Value

### Memory Remove (`memory_remove`)

Remove value from memory array

- **in** Index — Array identifier and index
- **out** Old Value — Removed value

### Memory Set (`memory_set`)

Set memory array value at a given index

- **in** Index — Array identifier and index
- **in** Value
- **out** Old — Previous value
