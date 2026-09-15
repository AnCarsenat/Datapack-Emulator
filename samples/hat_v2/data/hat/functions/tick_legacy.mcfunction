#-- 1.16.x only: replaceitem was removed in 1.17, so this file fails to load there
#-- item replace ... from does not exist yet: items move through a shulker box outside the world border
execute in minecraft:overworld unless block -30000000 0 1600 minecraft:shulker_box run forceload add -30000000 1600
execute in minecraft:overworld unless block -30000000 0 1600 minecraft:shulker_box run setblock -30000000 0 1600 minecraft:shulker_box
execute in minecraft:overworld run replaceitem block -30000000 0 1600 container.0 minecraft:air
execute in minecraft:overworld as @a[tag=hat,limit=1] run function hat:swap_legacy
