#-- Trigger checks
execute as @a if score @s hat matches 1 run tag @s add hat
#-- Only one of these loads: tick_legacy on 1.16.x (replaceitem), tick_modern on 1.17+ (item replace)
function hat:tick_legacy
function hat:tick_modern
tag @e remove hat



scoreboard players enable @a hat
scoreboard players set @a hat 0
