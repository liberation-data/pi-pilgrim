import asyncio
import gc
import itertools
import threading
import time
import unittest
import warnings

from pipilgrim import Assembled, AssemblyHolder, PilgrimAssembly, PilgrimError
from tests.utils.threads import run_in_threads


class Resource:
    """
    Closes with close(), and records the order it was closed in.
    """

    def __init__(self, log, name, uses=None):
        self.log = log
        self.name = name
        self.uses = uses
        self.closes = 0

    def close(self):
        self.closes += 1
        self.log.append(self.name)


class AsyncResource:
    def __init__(self, log, name):
        self.log = log
        self.name = name

    async def aclose(self):
        await asyncio.sleep(0)
        self.log.append(self.name)


class BothResource:
    """
    Has both aclose() and a sync close(), as some clients do.
    """

    def __init__(self, log):
        self.log = log

    async def aclose(self):
        self.log.append("aclose")

    def close(self):
        self.log.append("close")


class AsyncCloseResource:
    def __init__(self, log):
        self.log = log

    async def close(self):
        self.log.append("async close")


class ShutdownResource:
    def __init__(self, log):
        self.log = log

    def shutdown(self):
        self.log.append("shutdown")


class ContextResource:
    """
    A context manager with no close(): closing the assembly leaves it alone.
    """

    def __init__(self):
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.exited = True


class Failing:
    def __init__(self, log, name):
        self.log = log
        self.name = name

    def close(self):
        self.log.append(self.name)
        raise RuntimeError(f"{self.name} failed")


class Plain:
    pass


class PowerAssembly(PilgrimAssembly):
    def __init__(self):
        self.log = []
        super().__init__()

    def grid(self):
        return self.shared(lambda: Resource(self.log, "grid"))

    def plant(self):
        return self.shared(lambda: Resource(self.log, "plant", uses=self.grid()))

    def meter(self):
        return self.shared(lambda: Resource(self.log, "meter", uses=self.plant()))

    def weak_meter(self):
        return self.weak_shared(lambda: Resource(self.log, "weak meter"))

    def sized_meter(self, size):
        return self.weak_shared(lambda: Resource(self.log, f"meter {size}"))

    def spare(self):
        return self.object_graph(lambda: Resource(self.log, "spare"))

    def lamp(self):
        return self.unshared(lambda: Resource(self.log, "lamp"))

    def cable(self):
        return self.shared(lambda: AsyncResource(self.log, "cable"))

    def both(self):
        return self.shared(lambda: BothResource(self.log))

    def async_close(self):
        return self.shared(lambda: AsyncCloseResource(self.log))

    def turbine(self):
        return self.shared(lambda: ShutdownResource(self.log))

    def context(self):
        return self.shared(lambda: ContextResource())

    def plain(self):
        return self.shared(lambda: Plain())

    def alias(self):
        return self.shared(lambda: self.grid())

    def logged(self):
        return self.shared(lambda: Plain(), destroy=lambda it: self.log.append(("destroyed", it)))

    def async_logged(self):
        async def destroy(it):
            await asyncio.sleep(0)
            self.log.append("async destroy")

        return self.shared(lambda: Plain(), destroy=destroy)

    def awaitable_logged(self):
        async def destroy():
            self.log.append("awaited")

        # A plain function that returns a coroutine: close() can't tell it is async until it calls it.
        return self.shared(lambda: Plain(), destroy=lambda it: destroy())

    def left_open(self):
        return self.shared(lambda: Resource(self.log, "left open"), destroy=None)

    def failing(self, name):
        return self.shared(lambda: Failing(self.log, name))

    def asks_when_closed(self):
        return self.shared(lambda: Plain(), destroy=lambda it: self.grid())


class TestClosing(unittest.TestCase):
    def setUp(self):
        self.assembly = PowerAssembly()
        self.log = self.assembly.log

    def test_closes_shared_components_newest_first(self):
        self.assembly.meter()
        self.assembly.close()
        self.assertEqual(self.log, ["meter", "plant", "grid"])

    def test_closes_in_the_order_they_were_made_across_requests(self):
        self.assembly.grid()
        self.assembly.turbine()
        self.assembly.plant()
        self.assembly.close()
        self.assertEqual(self.log, ["plant", "shutdown", "grid"])

    def test_infers_close(self):
        grid = self.assembly.grid()
        self.assembly.close()
        self.assertEqual(grid.closes, 1)

    def test_infers_shutdown(self):
        self.assembly.turbine()
        self.assembly.close()
        self.assertEqual(self.log, ["shutdown"])

    def test_aclose_infers_aclose(self):
        self.assembly.cable()
        asyncio.run(self.assembly.aclose())
        self.assertEqual(self.log, ["cable"])

    def test_aclose_awaits_an_async_close(self):
        self.assembly.async_close()
        asyncio.run(self.assembly.aclose())
        self.assertEqual(self.log, ["async close"])

    def test_aclose_runs_a_sync_close(self):
        self.assembly.meter()
        asyncio.run(self.assembly.aclose())
        self.assertEqual(self.log, ["meter", "plant", "grid"])

    def test_aclose_prefers_aclose_and_close_prefers_close(self):
        self.assembly.both()
        asyncio.run(self.assembly.aclose())
        other = PowerAssembly()
        other.both()
        other.close()
        self.assertEqual(self.log, ["aclose"])
        self.assertEqual(other.log, ["close"])

    def test_leaves_a_context_manager_without_close_alone(self):
        context = self.assembly.context()
        self.assembly.close()
        self.assertFalse(context.exited)

    def test_keeps_no_record_of_a_component_with_no_teardown(self):
        self.assembly.plain()
        self.assembly.context()
        self.assertEqual(self.assembly._closeables, [])

    def test_runs_an_explicit_destroy_with_the_instance(self):
        instance = self.assembly.logged()
        self.assembly.close()
        self.assertEqual(self.log, [("destroyed", instance)])

    def test_aclose_awaits_an_async_destroy(self):
        self.assembly.async_logged()
        asyncio.run(self.assembly.aclose())
        self.assertEqual(self.log, ["async destroy"])

    def test_aclose_awaits_what_a_destroy_returns(self):
        self.assembly.awaitable_logged()
        asyncio.run(self.assembly.aclose())
        self.assertEqual(self.log, ["awaited"])

    def test_destroy_none_leaves_it_open(self):
        resource = self.assembly.left_open()
        self.assembly.close()
        self.assertEqual(resource.closes, 0)

    def test_does_not_close_object_graph_or_unshared_components(self):
        spare = self.assembly.spare()
        lamp = self.assembly.lamp()
        self.assembly.close()
        self.assertEqual((spare.closes, lamp.closes), (0, 0))

    def test_a_component_whose_getattr_raises_has_no_teardown(self):
        class Proxy:
            def __getattr__(self, name):
                raise RuntimeError("outside the context it needs")

        assembly = PilgrimAssembly()
        both = assembly.shared(lambda: (assembly.shared(lambda: Proxy()), assembly.shared(lambda: Plain())))
        self.assertIs(assembly.shared(lambda: None), both)
        self.assertEqual((assembly._made, assembly._closeables), ({}, []))
        assembly.close()

    def test_a_component_that_is_a_class_isnt_closed(self):
        class Registry:
            closed = False

            @classmethod
            def shutdown(cls):
                cls.closed = True

        class Assembly(PilgrimAssembly):
            def resource_type(self):
                return self.shared(lambda: Resource)

            def registry(self):
                return self.shared(lambda: Registry)

        assembly = Assembly()
        assembly.resource_type()
        assembly.registry()
        assembly.close()
        self.assertFalse(Registry.closed)
        self.assertEqual(assembly._closeables, [])

    def test_closes_one_instance_once_under_two_names(self):
        grid = self.assembly.alias()
        self.assertIs(grid, self.assembly.grid())
        self.assembly.close()
        self.assertEqual(grid.closes, 1)

    def test_closes_a_live_weak_shared_component(self):
        meter = self.assembly.weak_meter()
        self.assembly.close()
        self.assertEqual(meter.closes, 1)

    def test_skips_a_weak_shared_component_already_collected(self):
        self.assembly.weak_meter()
        gc.collect()
        self.assembly.close()
        self.assertEqual(self.log, [])

    def test_runs_a_weak_shared_components_destroy_or_leaves_it_open(self):
        class Assembly(PilgrimAssembly):
            def logged(self):
                return self.weak_shared(lambda: Plain(), destroy=lambda it: log.append(("destroyed", it)))

            def left_open(self):
                return self.weak_shared(lambda: Resource(log, "left open"), destroy=None)

        log = []
        assembly = Assembly()
        logged, left_open = assembly.logged(), assembly.left_open()
        assembly.close()
        self.assertEqual(log, [("destroyed", logged)])
        self.assertEqual(left_open.closes, 0)

    def test_forgets_collected_weak_shared_components(self):
        for size in range(1000):
            self.assembly.sized_meter(size)
        gc.collect()
        self.assembly.sized_meter("last")
        self.assertLess(len(self.assembly._closeables), 200)
        kept = self.assembly.sized_meter("kept")
        self.assembly.close()
        self.assertEqual(kept.closes, 1)

    def test_is_a_context_manager(self):
        with PowerAssembly() as assembly:
            grid = assembly.grid()
        self.assertEqual(grid.closes, 1)

    def test_is_an_async_context_manager(self):
        async def run():
            async with PowerAssembly() as assembly:
                assembly.cable()
            return assembly.log

        self.assertEqual(asyncio.run(run()), ["cable"])


class TestClosingTwice(unittest.TestCase):
    def setUp(self):
        self.assembly = PowerAssembly()

    def test_closing_twice_closes_once(self):
        grid = self.assembly.grid()
        self.assembly.close()
        self.assembly.close()
        asyncio.run(self.assembly.aclose())
        self.assertEqual(grid.closes, 1)

    def test_a_second_aclose_returns_while_the_first_is_still_closing(self):
        async def run():
            self.assembly.cable()
            first = asyncio.create_task(self.assembly.aclose())
            await asyncio.sleep(0)
            await self.assembly.aclose()
            log = list(self.assembly.log)
            await first
            return log

        self.assertEqual(asyncio.run(run()), [])
        self.assertEqual(self.assembly.log, ["cable"])

    def test_a_closed_assembly_makes_no_more_components(self):
        self.assembly.grid()
        self.assembly.weak_meter()
        self.assembly.close()
        for it in (self.assembly.grid, self.assembly.weak_meter, self.assembly.spare, self.assembly.lamp):
            with self.subTest(it.__name__):
                with self.assertRaisesRegex(PilgrimError, "PowerAssembly is closed, so it makes no more components"):
                    it()

    def test_lets_go_of_its_instances(self):
        self.assembly.grid()
        self.assembly.close()
        self.assertEqual((self.assembly._shared, self.assembly._closeables), ({}, []))

    def test_a_destroy_that_asks_for_a_component_is_refused(self):
        self.assembly.asks_when_closed()
        with self.assertRaises(ExceptionGroup) as caught:
            self.assembly.close()
        self.assertRegex(str(caught.exception.exceptions[0]), "is closed")

    def test_cant_close_while_making_a_component(self):
        class Closer(PilgrimAssembly):
            def self_closing(self):
                return self.shared(lambda: self.close())

        with self.assertRaisesRegex(PilgrimError, "can't close while it is making a component"):
            Closer().self_closing()


class TestClosingErrors(unittest.TestCase):
    def setUp(self):
        self.assembly = PowerAssembly()
        self.log = self.assembly.log

    def test_a_failed_teardown_doesnt_stop_the_others(self):
        self.assembly.grid()
        self.assembly.failing("first")
        self.assembly.plant()
        self.assembly.failing("second")
        with self.assertRaises(ExceptionGroup) as caught:
            self.assembly.close()
        self.assertEqual(self.log, ["second", "plant", "first", "grid"])
        group = caught.exception
        self.assertEqual(str(group), "PowerAssembly closed, but 2 teardowns failed (2 sub-exceptions)")
        self.assertEqual([str(it) for it in group.exceptions], ["second failed", "first failed"])
        self.assertEqual(
            group.exceptions[0].__notes__, ["While closing shared(PowerAssembly.failing('second'))"]
        )

    def test_aclose_gathers_errors_too(self):
        self.assembly.failing("only")
        self.assembly.cable()
        with self.assertRaises(ExceptionGroup) as caught:
            asyncio.run(self.assembly.aclose())
        self.assertEqual(self.log, ["cable", "only"])
        self.assertEqual(str(caught.exception), "PowerAssembly closed, but 1 teardown failed (1 sub-exception)")

    def test_a_failed_close_still_closes_the_assembly(self):
        self.assembly.failing("only")
        with self.assertRaises(ExceptionGroup):
            self.assembly.close()
        self.assembly.close()
        self.assertEqual(self.log, ["only"])


class TestSyncCloseOfAsyncTeardown(unittest.TestCase):
    def setUp(self):
        self.assembly = PowerAssembly()
        self.log = self.assembly.log

    def assertRefused(self, components):
        with self.assertRaisesRegex(
            PilgrimError,
            rf"PowerAssembly can't be closed with close\(\), since the teardown of {components} is async. "
            rf"Close it with await aclose\(\).",
        ):
            self.assembly.close()

    def test_refuses_aclose_before_closing_anything(self):
        grid = self.assembly.grid()
        self.assembly.cable()
        self.assertRefused(r"shared\(PowerAssembly.cable\)")
        self.assertEqual(grid.closes, 0)
        self.assertIs(self.assembly.grid(), grid)
        asyncio.run(self.assembly.aclose())
        self.assertEqual(self.log, ["cable", "grid"])

    def test_refuses_an_async_close(self):
        self.assembly.async_close()
        self.assertRefused(r"shared\(PowerAssembly.async_close\)")

    def test_refuses_an_async_destroy(self):
        self.assembly.async_logged()
        self.assertRefused(r"shared\(PowerAssembly.async_logged\)")

    def test_a_destroy_that_returns_an_awaitable_is_an_error(self):
        self.assembly.awaitable_logged()
        self.assembly.grid()
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # the coroutine is closed, not left unawaited
            with self.assertRaises(ExceptionGroup) as caught:
                self.assembly.close()
            gc.collect()
        self.assertRegex(
            str(caught.exception.exceptions[0]),
            r"The teardown of shared\(PowerAssembly.awaitable_logged\) returned an awaitable",
        )
        self.assertEqual(self.log, ["grid"])

    def test_a_destroy_that_returns_another_awaitable_is_an_error(self):
        class Awaitable:
            def __await__(self):
                yield

        assembly = PilgrimAssembly()
        assembly.shared(lambda: Plain(), destroy=lambda it: Awaitable())
        with self.assertRaises(ExceptionGroup) as caught:
            assembly.close()
        self.assertRegex(str(caught.exception.exceptions[0]), "returned an awaitable, which close\\(\\) can't wait for")

    def test_refuses_for_an_async_teardown_in_a_component_assembly(self):
        class Outer(PilgrimAssembly):
            def inner(self):
                return self.shared(lambda: PowerAssembly())

        outer = Outer()
        outer.inner().cable()
        with self.assertRaisesRegex(PilgrimError, r"teardown of shared\(PowerAssembly.cable\) is async"):
            outer.close()


class FakePool:
    """
    Like a database driver's pool: made on the event loop, and closed on it before the loop ends.
    """

    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.connections = ["a", "b"]

    async def aclose(self):
        assert asyncio.get_running_loop() is self.loop, "closed on another loop"
        while self.connections:
            self.connections.pop()
            await asyncio.sleep(0)


class Repository:
    def __init__(self, pool):
        self.pool = pool
        self.closed_with_pool_open = None

    def close(self):
        self.closed_with_pool_open = bool(self.pool.connections)


class DatabaseAssembly(PilgrimAssembly):
    def pool(self):
        return self.shared(lambda: FakePool())

    def repository(self):
        return self.shared(lambda: Repository(self.pool()))


class TestAsyncResource(unittest.TestCase):
    def test_async_with_closes_the_pool_before_the_loop_ends(self):
        async def application():
            async with DatabaseAssembly() as assembly:
                repository = assembly.repository()
                await asyncio.sleep(0)
            return repository

        repository = asyncio.run(application())
        self.assertEqual(repository.pool.connections, [])
        self.assertTrue(repository.closed_with_pool_open)

    def test_close_refuses_the_pool(self):
        async def application():
            assembly = DatabaseAssembly()
            pool = assembly.pool()
            with self.assertRaisesRegex(PilgrimError, "Close it with await aclose"):
                assembly.close()
            await assembly.aclose()
            return pool

        self.assertEqual(asyncio.run(application()).connections, [])


class InitAssembly(PilgrimAssembly):
    def __init__(self):
        self.log = []
        super().__init__()

    def opened(self):
        return self.shared(
            lambda: Resource(self.log, "opened"),
            configure=lambda it: self.log.append("configured"),
            init=lambda it: self.log.append(("init", it.name)),
        )

    def user(self):
        def make():
            self.log.append(("user sees", self.opened().name))
            return Plain()

        return self.shared(make)

    def each(self):
        return self.unshared(lambda: Plain(), init=lambda it: self.log.append("init"))

    def spare(self):
        return self.object_graph(lambda: Plain(), init=lambda it: self.log.append("init spare"))

    def weak(self):
        return self.weak_shared(lambda: Resource(self.log, "weak"), init=lambda it: self.log.append("init weak"))

    def broken(self):
        def fail(it):
            raise ValueError("init failed")

        return self.shared(lambda: Resource(self.log, "broken"), init=fail)


class TestInit(unittest.TestCase):
    def setUp(self):
        self.assembly = InitAssembly()
        self.log = self.assembly.log

    def test_init_runs_once_before_dependents_get_it_and_configure_after_the_graph(self):
        self.assembly.user()
        self.assembly.user()
        self.assembly.opened()
        self.assertEqual(self.log, [("init", "opened"), ("user sees", "opened"), "configured"])

    def test_init_runs_for_each_instance(self):
        self.assembly.each()
        self.assembly.each()
        self.assembly.spare()
        held = self.assembly.weak()
        self.assembly.weak()
        self.assertEqual(self.log, ["init", "init", "init spare", "init weak"])
        del held

    def test_a_failed_init_keeps_nothing(self):
        with self.assertRaisesRegex(ValueError, "init failed"):
            self.assembly.broken()
        self.assertEqual(self.assembly._closeables, [])
        self.assembly.close()
        self.assertEqual(self.log, [])


class TestClosingWithThreads(unittest.TestCase):
    def test_closing_while_threads_make_components(self):
        for _ in range(20):
            assembly = PowerAssembly()
            made = []
            unexpected = []
            ready = threading.Barrier(5)

            def make():
                ready.wait()
                # Until the assembly refuses, so the close lands while the threads are making components.
                for size in itertools.count():
                    try:
                        made.append(assembly.sized_meter(size))
                        made.append(assembly.meter())
                        made.append(assembly.lamp())
                    except PilgrimError as e:
                        if "is closed" not in str(e):
                            unexpected.append(e)
                        return
                    except BaseException as e:
                        unexpected.append(e)
                        return

            threads = [threading.Thread(target=make) for _ in range(4)]
            for it in threads:
                it.start()
            ready.wait()
            time.sleep(0.0005)
            assembly.close()
            for it in threads:
                it.join()
            self.assertEqual(unexpected, [])
            self.assertGreater(len(made), 3)
            # Every shared and weak_shared instance a thread got is closed once, and nothing is made after the close.
            self.assertEqual({it.closes for it in made if it.name != "lamp"}, {1})
            self.assertEqual({it.closes for it in made if it.name == "lamp"}, {0})
            self.assertEqual(assembly._shared, {})
            self.assertEqual(assembly._closeables, [])

    def test_close_waits_for_a_request_in_progress_and_closes_what_it_made(self):
        log = []
        started = threading.Event()

        class Slow(PilgrimAssembly):
            def resource(self):
                def make():
                    started.set()
                    time.sleep(0.05)
                    return Resource(log, "slow")

                return self.shared(make)

        assembly = Slow()
        got = []
        maker = threading.Thread(target=lambda: got.append(assembly.resource()))
        maker.start()
        started.wait()
        assembly.close()
        maker.join()
        self.assertEqual(got[0].closes, 1)

    def test_closes_once_when_threads_close_together(self):
        assembly = PowerAssembly()
        grid = assembly.grid()
        run_in_threads(assembly.close, 8)
        self.assertEqual(grid.closes, 1)


class Supply:
    def __init__(self, log, name):
        self.log = log
        self.name = name

    def close(self):
        self.log.append(self.name)


LOG = []


class SupplyAssembly(PilgrimAssembly):
    def make_bindings(self):
        self.make_injectable(self.supply, by_type=Supply)

    def supply(self) -> Supply:
        return self.shared(lambda: Supply(LOG, "supply"))


class AsyncSupplyAssembly(PilgrimAssembly):
    def cable(self):
        return self.shared(lambda: AsyncResource(LOG, "async supply"))


class CityAssembly(PilgrimAssembly):
    def __init__(self):
        self.supplies = AssemblyHolder.shared(SupplyAssembly)
        super().__init__()

    def city(self):
        return self.shared(lambda: Supply(LOG, f"city on {self.supplies.supply().name}"))


class TownAssembly(PilgrimAssembly):
    def town(self):
        return self.shared(lambda: Supply(LOG, f"town on {AssemblyHolder.shared(SupplyAssembly).supply().name}"))


class Consumer:
    supply: Supply = Assembled(assembly=SupplyAssembly)


class TestNestedAssemblies(unittest.TestCase):
    def setUp(self):
        AssemblyHolder.reset()
        LOG.clear()

    def tearDown(self):
        AssemblyHolder.reset()

    def test_an_assembly_closes_one_it_asked_the_holder_for_after_its_own_components(self):
        city = CityAssembly()
        city.city()
        city.close()
        self.assertEqual(LOG, ["city on supply", "supply"])

    def test_the_holder_forgets_a_closed_assembly(self):
        city = CityAssembly()
        city.close()
        fresh = AssemblyHolder.shared(SupplyAssembly)
        self.assertIsNot(fresh, city.supplies)
        self.assertIsNot(fresh.supply(), None)

    def test_an_assembly_owns_one_it_asked_for_from_a_factory(self):
        town = TownAssembly()
        town.town()
        town.close()
        self.assertEqual(LOG, ["town on supply", "supply"])

    def test_an_assembly_doesnt_close_one_something_else_asked_for_first(self):
        Consumer().supply
        city = CityAssembly()
        city.city()
        city.close()
        self.assertEqual(LOG, ["city on supply"])
        self.assertIs(AssemblyHolder.shared(SupplyAssembly), city.supplies)

    def test_the_holder_closes_every_assembly_newest_first(self):
        AssemblyHolder.shared(CityAssembly).city()
        AssemblyHolder.shared(TownAssembly).town()
        AssemblyHolder.close()
        self.assertEqual(LOG, ["town on supply", "city on supply", "supply"])
        self.assertEqual(AssemblyHolder._instances, {})

    def test_the_holder_closes_an_assembly_before_one_it_asked_for_after_it_was_made(self):
        AssemblyHolder.shared(TownAssembly).town()
        AssemblyHolder.close()
        self.assertEqual(LOG, ["town on supply", "supply"])
        self.assertEqual(AssemblyHolder._instances, {})

    def test_the_holder_acloses_an_assembly_before_one_it_asked_for_after_it_was_made(self):
        AssemblyHolder.shared(TownAssembly).town()
        asyncio.run(AssemblyHolder.aclose())
        self.assertEqual(LOG, ["town on supply", "supply"])

    def test_an_owned_assemblys_errors_are_raised_with_the_owners(self):
        class Broken(PilgrimAssembly):
            def failing(self):
                return self.shared(lambda: Failing(LOG, "owned"))

        class Owner(PilgrimAssembly):
            def __init__(self):
                self.broken = AssemblyHolder.shared(Broken)
                super().__init__()

            def failing(self):
                return self.shared(lambda: Failing(LOG, "owner"))

        for name, close in (("close", lambda it: it.close()), ("aclose", lambda it: asyncio.run(it.aclose()))):
            with self.subTest(name):
                AssemblyHolder.reset()
                LOG.clear()
                owner = Owner()
                owner.failing()
                owner.broken.failing()
                with self.assertRaises(ExceptionGroup) as caught:
                    close(owner)
                self.assertEqual(LOG, ["owner", "owned"])
                own, owned = caught.exception.exceptions
                self.assertEqual(str(own), "owner failed")
                self.assertEqual(str(owned.exceptions[0]), "owned failed")

    def test_the_holder_closes_async_teardowns_with_aclose(self):
        AssemblyHolder.shared(CityAssembly).city()
        AssemblyHolder.shared(AsyncSupplyAssembly).cable()
        asyncio.run(AssemblyHolder.aclose())
        self.assertEqual(LOG, ["async supply", "city on supply", "supply"])

    def test_the_holder_refuses_async_teardowns_before_closing_anything(self):
        AssemblyHolder.shared(CityAssembly).city()
        AssemblyHolder.shared(AsyncSupplyAssembly).cable()
        with self.assertRaisesRegex(PilgrimError, r"AssemblyHolder can't be closed with close\(\)"):
            AssemblyHolder.close()
        self.assertEqual(LOG, [])

    def test_the_holder_gathers_errors_from_every_assembly(self):
        class Broken(PilgrimAssembly):
            def failing(self):
                return self.shared(lambda: Failing(LOG, "broken"))

        AssemblyHolder.shared(CityAssembly).city()
        AssemblyHolder.shared(Broken).failing()
        with self.assertRaises(ExceptionGroup) as caught:
            AssemblyHolder.close()
        self.assertEqual(LOG, ["broken", "city on supply", "supply"])
        self.assertEqual(str(caught.exception.exceptions[0].exceptions[0]), "broken failed")

    def test_closes_a_component_that_is_an_assembly(self):
        class Grid(PilgrimAssembly):
            def city(self):
                return self.shared(lambda: CityAssembly())

        grid = Grid()
        grid.city().city()
        grid.close()
        self.assertEqual(LOG, ["city on supply", "supply"])

    def test_aclose_closes_owned_assemblies_with_aclose(self):
        class Owner(PilgrimAssembly):
            def __init__(self):
                self.supply = AssemblyHolder.shared(AsyncSupplyAssembly)
                super().__init__()

        owner = Owner()
        owner.supply.cable()
        with self.assertRaisesRegex(PilgrimError, r"teardown of shared\(AsyncSupplyAssembly.cable\) is async"):
            owner.close()
        asyncio.run(owner.aclose())
        self.assertEqual(LOG, ["async supply"])

    def test_closes_two_assemblies_that_hold_each_other(self):
        class Left(PilgrimAssembly):
            def right(self):
                return self.shared(lambda: AssemblyHolder.shared(Right))

            def supply(self):
                return self.shared(lambda: Supply(LOG, "left"))

        class Right(PilgrimAssembly):
            def left(self):
                return self.shared(lambda: AssemblyHolder.shared(Left))

        for name, close in (
            ("close", lambda it: it.close()),
            ("holder", lambda it: AssemblyHolder.close()),
            ("aclose", lambda it: asyncio.run(it.aclose())),
        ):
            with self.subTest(name):
                AssemblyHolder.reset()
                LOG.clear()
                left = AssemblyHolder.shared(Left)
                left.supply()
                left.right().left()
                close(left)
                self.assertEqual(LOG, ["left"])
                self.assertEqual(AssemblyHolder._instances, {})

    def test_the_holder_owns_an_assembly_asked_for_where_no_method_is_on_the_stack(self):
        thread = threading.Thread(target=AssemblyHolder.shared, args=(SupplyAssembly,))
        thread.start()
        thread.join()
        city = CityAssembly()
        city.close()
        self.assertIs(AssemblyHolder.shared(SupplyAssembly), city.supplies)

    def test_the_holder_owns_an_assembly_a_component_asks_for_while_it_is_made(self):
        class Service:
            def __init__(self):
                self.supplies = AssemblyHolder.shared(SupplyAssembly)

        class ServiceAssembly(PilgrimAssembly):
            def service(self):
                return self.shared(lambda: Service())

        services = ServiceAssembly()
        supplies = services.service().supplies
        self.assertEqual(services._owned, [])
        services.close()
        self.assertIs(AssemblyHolder.shared(SupplyAssembly), supplies)

    def test_closing_a_forgotten_assembly_doesnt_forget_the_new_one(self):
        old = AssemblyHolder.shared(SupplyAssembly)
        AssemblyHolder.reset()
        new = AssemblyHolder.shared(SupplyAssembly)
        old.close()
        self.assertIs(AssemblyHolder.shared(SupplyAssembly), new)

    def test_an_owner_closes_after_an_assembly_it_owns_closed_itself(self):
        class Owner(PilgrimAssembly):
            def __init__(self):
                self.supply = AssemblyHolder.shared(AsyncSupplyAssembly)
                super().__init__()

        owner = Owner()
        owner.supply.cable()
        asyncio.run(owner.supply.aclose())
        owner.close()
        self.assertEqual(LOG, ["async supply"])

    def test_the_holder_gathers_errors_from_every_assembly_with_aclose(self):
        class Broken(PilgrimAssembly):
            def failing(self):
                return self.shared(lambda: Failing(LOG, "broken"))

        AssemblyHolder.shared(CityAssembly).city()
        AssemblyHolder.shared(Broken).failing()
        with self.assertRaises(ExceptionGroup) as caught:
            asyncio.run(AssemblyHolder.aclose())
        self.assertEqual(LOG, ["broken", "city on supply", "supply"])
        self.assertEqual(
            str(caught.exception), "Closing the held assemblies failed: 1 of them raised (1 sub-exception)"
        )


if __name__ == "__main__":
    unittest.main()
