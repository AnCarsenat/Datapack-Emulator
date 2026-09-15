#-- 1.17+ only: uses item replace, so this file fails to load on 1.16.x
execute run item replace entity @e[tag= hat_saver] weapon from entity @e[tag=hat,limit=1] armor.head
execute as @e[tag=hat,limit=1] run item replace entity @s armor.head from entity @s weapon
execute as @e[tag=hat,limit=1] run item replace entity @s weapon from entity @e[tag= hat_saver,limit=1] weapon
item replace entity @e[tag=hat_saver,type=minecraft:armor_stand] weapon with air

execute unless entity @e[tag=hat_saver,type=minecraft:armor_stand] run summon minecraft:armor_stand 0 300 0 {NoGravity:1,Invulnerable:1b,Invisible:1b,Tags:["hat_saver"]}
