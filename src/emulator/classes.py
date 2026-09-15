import os
from logging import log


class Datapack:
    def __init__(self, path:os.PathLike):
        
        pass
        self.path = path

    def load(datapack_name:str):
        raise(NotImplementedError)

class PackMCMETA:
    def __init__(self, path):
        pass
        self.path = path
        with open(path,"r") as file:
            self.content = file.read()

    def to_minecraft_version()->str:
        pass

class PackPNG:
    def __init__(self):
        pass

class Ressource:
    def __init__(self):
        pass

class Recipe(Ressource):
    def __init__(self):
        super().__init__()

class Advancement(Ressource):
    def __init__(self):
        super().__init__()


class Namespace:
    def __init__(self):
        pass

class Entity:
    def __init__(self):
        pass

class Particle:
    def __init__(self):
        pass

class Sound:
    def __init__(self):
        pass



class Command:
    def __init__(self):
        raise(NotImplementedError)

    @staticmethod
    def execute_command():
        ...

    @staticmethod
    def say_command(full_command_string:str)->None:
        full_command_string = full_command_string.split(" ")[1:]
        log(0, "ran say command :")
        log(1,full_command_string)

    @staticmethod
    def tellraw_command(full_command_string:str)->None:
        full_command_string = full_command_string.split(" ")[1:]
        log(0, "ran say command :")
        log(1,full_command_string)

    @staticmethod
    def summon_command(full_command_string:str)->None:
        full_command_string = full_command_string.split(" ")[1:]
        log(0, "ran summon command :")
        log(1, f"summoned {full_command_string}")

    commands = {
        "execute": execute_command,
        "say":1,

    }

    def run(self):
        raise(NotImplementedError)



class Function:
    def __init__(self,path:str):
        self.path = path
        self.name = NotImplementedError
        # If self.path = ~/packemulator/samples/pack1/data/namespace1/function/function1
        # self.name = namespace1:function1

        with open(path,"r") as file:
            content = [Command]
            for line in file.readlines():
                content += Command(line)
            self.content:list[Command] = content

        
    
    def execute(self):
        execution_time = 0.0
        for command in self.content:
            result = command.run()
            execution_time = result
        