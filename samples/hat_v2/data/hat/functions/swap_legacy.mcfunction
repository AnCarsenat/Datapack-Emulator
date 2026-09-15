#-- 1.16.x only, called by tick_legacy as the player
#-- Mining the box with a hat_drop_contents stick drops only its contents (see loot_tables/blocks/shulker_box.json)
data remove storage hat:swap head
data modify storage hat:swap head set from entity @s Inventory[{Slot:103b}]

#-- Held item to head
data modify block -30000000 0 1600 Items append from entity @s SelectedItem
loot replace entity @s armor.head mine -30000000 0 1600 minecraft:stick{hat_drop_contents:1b}
replaceitem block -30000000 0 1600 container.0 minecraft:air

#-- Old head item to hand
execute if data storage hat:swap head run data modify storage hat:swap head.Slot set value 0b
data modify block -30000000 0 1600 Items append from storage hat:swap head
loot replace entity @s weapon.mainhand mine -30000000 0 1600 minecraft:stick{hat_drop_contents:1b}
replaceitem block -30000000 0 1600 container.0 minecraft:air
