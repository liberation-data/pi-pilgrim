import asyncio
import unittest

from pipilgrim import Assembled, AssemblyHolder, AsyncPilgrimAssembly, PilgrimAssembly, PilgrimConfigurable, PilgrimError

# The README's examples, adapted to run together: names differ where examples would clash.


class Map:
    def __init__(self, region):
        self.region = region


class FakeMap(Map):
    def __init__(self):
        super().__init__("Nowhere")


class Horse:
    def __init__(self, name):
        self.name = name


class Squire:
    def __init__(self, horse=None):
        self.horse = horse
        self.master = None


class Knight:
    def __init__(self, quest=None, horse=None, squire=None, name=None):
        self.quest = quest
        self.horse = horse
        self.squire = squire
        self.name = name


class HolyGrailQuest:
    def __init__(self, map=None):
        self.map = map


class Kingdom:
    def __init__(self, region):
        self.region = region


class MapAssembly(PilgrimAssembly):
    def make_bindings(self):
        self.make_injectable(self.map, by_type=Map)

    def map(self) -> Map:
        return self.shared(lambda: Map(region="Britain"))


class QuestAssembly(PilgrimAssembly):
    def __init__(self):
        self.maps = AssemblyHolder.shared(MapAssembly)
        super().__init__()

    def make_bindings(self):
        self.make_injectable(self.knight, by_type=Knight)
        self.make_injectable(self.quest, by_key="damsel_quest")
        self.import_bindings(self.maps)

    def knight(self) -> Knight:
        return self.unshared(lambda: Knight(quest=self.quest(), horse=self.horse(), squire=self.squire()))

    def squire(self) -> Squire:
        return self.unshared(lambda: Squire(horse=self.horse()))

    def quest(self) -> HolyGrailQuest:
        return self.shared(lambda: HolyGrailQuest(map=self.map()))

    def horse(self) -> Horse:
        return self.object_graph(lambda: Horse(name="Bucephalus"))

    def map(self) -> Map:
        return self.shared(lambda: self.maps.map())

    def knight_named(self, name: str) -> Knight:
        return self.unshared(lambda: Knight(name=name, quest=self.quest()))

    def kingdom(self, region: str) -> Kingdom:
        return self.shared(lambda: Kingdom(region=region))


class CourtAssembly(PilgrimAssembly):
    def knight(self) -> Knight:
        return self.shared(lambda: Knight(squire=self.squire()))

    def squire(self) -> Squire:
        return self.shared(
            lambda: Squire(),
            configure=lambda squire: setattr(squire, "master", self.knight()),
        )


class LoyalSquire(Squire, PilgrimConfigurable):
    def configure(self, assembly):
        self.master = assembly.knight()


class LoyalCourtAssembly(PilgrimAssembly):
    def knight(self) -> Knight:
        return self.shared(lambda: Knight(squire=self.squire()))

    def squire(self) -> Squire:
        return self.shared(lambda: LoyalSquire())


class TangledAssembly(PilgrimAssembly):
    def knight(self) -> Knight:
        return self.shared(lambda: Knight(squire=self.squire()))

    def squire(self) -> Squire:
        return self.shared(lambda: Squire(horse=self.knight()))


class TestQuestAssembly(QuestAssembly):
    def map(self) -> Map:
        return self.shared(lambda: FakeMap())


class RecordingMap(Map):
    def __init__(self, real):
        super().__init__(real.region)
        self.real = real


class RecordingQuestAssembly(QuestAssembly):
    def map(self) -> Map:
        real = super().map
        return self.shared(lambda: RecordingMap(real()))


class Tavern:
    knight: Knight = Assembled()
    quest: HolyGrailQuest = Assembled(key="damsel_quest")
    map: Map = Assembled(assembly=MapAssembly)
    assembly: QuestAssembly = Assembled()


class Pool:
    def __init__(self, url):
        self.url = url
        self.log = []

    async def connect(self):
        self.log.append("connect")

    async def aclose(self):
        self.log.append("pool")


class QuestRepository:
    def __init__(self, pool):
        self.pool = pool
        self.saved = []

    async def save(self, quest):
        self.saved.append(quest)

    def close(self):
        self.pool.log.append("repository")


class DatabaseAssembly(PilgrimAssembly):
    def pool(self) -> Pool:
        return self.shared(lambda: Pool(url="postgresql://localhost/quests"))

    def repository(self) -> QuestRepository:
        return self.shared(lambda: QuestRepository(self.pool()))


class PoolAssembly(AsyncPilgrimAssembly):
    def pool(self) -> Pool:
        return self.shared(lambda: Pool(url="postgresql://localhost/quests"))


class Ledger:
    def __init__(self):
        self.log = []

    def open(self):
        self.log.append("open")

    def flush_and_close(self):
        self.log.append("flush and close")


class Scroll:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class LedgerAssembly(PilgrimAssembly):
    def ledger(self) -> Ledger:
        return self.shared(lambda: Ledger(), init=lambda it: it.open(), destroy=lambda it: it.flush_and_close())

    def scroll(self) -> Scroll:
        return self.shared(lambda: Scroll(), destroy=None)  # closed by someone else


class TestReadme(unittest.TestCase):
    def setUp(self):
        AssemblyHolder.reset()

    def tearDown(self):
        AssemblyHolder.reset()

    def test_quick_start(self):
        assembly = QuestAssembly()
        knight = assembly.knight()
        self.assertEqual("Britain", knight.quest.map.region)
        self.assertIs(knight.quest, assembly.knight().quest)

    def test_object_graph_is_shared_within_one_knight(self):
        assembly = QuestAssembly()
        first, second = assembly.knight(), assembly.knight()
        self.assertIs(first.horse, first.squire.horse)
        self.assertIsNot(first.horse, second.horse)

    def test_runtime_arguments(self):
        assembly = QuestAssembly()
        self.assertEqual("Lancelot", assembly.knight_named("Lancelot").name)
        self.assertIsNot(assembly.knight_named("Lancelot"), assembly.knight_named("Lancelot"))
        self.assertIs(assembly.kingdom("Wessex"), assembly.kingdom("Wessex"))
        self.assertIsNot(assembly.kingdom("Wessex"), assembly.kingdom("Mercia"))

    def test_configure_breaks_a_cycle(self):
        knight = CourtAssembly().knight()
        self.assertIs(knight, knight.squire.master)

    def test_configurable_breaks_a_cycle(self):
        knight = LoyalCourtAssembly().knight()
        self.assertIs(knight, knight.squire.master)

    def test_a_cycle_through_initializers_is_named(self):
        with self.assertRaises(PilgrimError) as caught:
            TangledAssembly().knight()
        self.assertEqual(
            "Circular dependency in initializers: shared(TangledAssembly.knight) -> shared(TangledAssembly.squire) "
            "-> shared(TangledAssembly.knight). Break it with configure=.",
            str(caught.exception),
        )

    def test_assembled(self):
        AssemblyHolder.default_assembly_type = QuestAssembly
        tavern = Tavern()
        self.assertIsInstance(tavern.knight, Knight)
        self.assertIs(AssemblyHolder.shared(QuestAssembly).quest(), tavern.quest)
        self.assertIs(AssemblyHolder.shared(MapAssembly).map(), tavern.map)
        self.assertIs(AssemblyHolder.shared(QuestAssembly), tavern.assembly)

    def test_imported_bindings_inject_through_the_importer(self):
        imported = AssemblyHolder.shared(QuestAssembly).factory_for(Map)
        self.assertIs(AssemblyHolder.shared(MapAssembly).map(), imported())

    def test_overriding_one_component(self):
        self.assertEqual("Nowhere", TestQuestAssembly().knight().quest.map.region)

    def test_an_override_builds_on_super(self):
        assembly = RecordingQuestAssembly()
        recorded = assembly.knight().quest.map
        self.assertIsInstance(recorded, RecordingMap)
        self.assertIs(AssemblyHolder.shared(MapAssembly).map(), recorded.real)
        self.assertIs(recorded, assembly.map())

    def test_closing(self):
        async def main():
            async with DatabaseAssembly() as assembly:
                repository = assembly.repository()
                await repository.save("grail")
            return repository

        repository = asyncio.run(main())
        self.assertEqual(["grail"], repository.saved)
        self.assertEqual(["repository", "pool"], repository.pool.log)

    def test_an_async_assembly_closes_with_await_close(self):
        async def main():
            assembly = PoolAssembly()
            await assembly.pool().connect()
            pool = assembly.pool()
            await assembly.close()
            return pool

        self.assertEqual(["connect", "pool"], asyncio.run(main()).log)

    def test_init_and_destroy(self):
        with LedgerAssembly() as assembly:
            ledger, scroll = assembly.ledger(), assembly.scroll()
        self.assertEqual(["open", "flush and close"], ledger.log)
        self.assertFalse(scroll.closed)

    def test_closing_an_assembly_closes_the_one_it_asked_for(self):
        quests = QuestAssembly()
        quests.close()
        with self.assertRaisesRegex(PilgrimError, "MapAssembly is closed"):
            quests.maps.map()
        self.assertIsNot(AssemblyHolder.shared(MapAssembly), quests.maps)

    def test_the_holder_closes_what_it_holds(self):
        AssemblyHolder.default_assembly_type = QuestAssembly
        Tavern().knight
        asyncio.run(AssemblyHolder.aclose())
        self.assertEqual({}, AssemblyHolder._instances)


if __name__ == "__main__":
    unittest.main()
