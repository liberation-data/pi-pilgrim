from abc import ABC, abstractmethod


class Quest(ABC):
    @abstractmethod
    def describe(self) -> str:
        pass


class DamselInDistressQuest(Quest):
    def describe(self) -> str:
        return "The damsel is a comely fellow named Bruce, the knight is none other than the fearsome Fiona"


class HolyGrailQuest(Quest):

    def __init__(self, description: str):
        self.description = description

    def describe(self) -> str:
        return self.description

