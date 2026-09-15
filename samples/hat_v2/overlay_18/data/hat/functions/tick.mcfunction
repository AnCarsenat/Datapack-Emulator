#-- Trigger checks
execute as @a if score @s hat matches 1 run tag @s add hat
function hat:tick_modern
tag @e remove hat



scoreboard players enable @a hat
scoreboard players set @a hat 0
