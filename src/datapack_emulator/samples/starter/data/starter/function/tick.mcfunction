# runs every tick
scoreboard players add #ticks ticks 1
execute if score #ticks ticks matches 100 run function starter:every_five_seconds
