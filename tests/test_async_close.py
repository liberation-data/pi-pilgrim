import asyncio
import contextlib
import gc
import unittest
import warnings

from pipilgrim import AssemblyHolder, AsyncPilgrimAssembly, AsyncTeardownRequired, PilgrimAssembly, PilgrimError

REFUSAL = "is async. Close it with await aclose()."


class Resource:
    def __init__(self, log, name):
        self.log = log
        self.name = name

    def close(self):
        self.log.append(self.name)


class Pool:
    def __init__(self, log):
        self.log = log

    async def aclose(self):
        await asyncio.sleep(0)
        self.log.append("pool")


class AsyncDatabase(AsyncPilgrimAssembly):
    """
    Like drivine-python's DrivineAssembly: a pool that closes asynchronously, and a component that closes in sync.
    """

    def __init__(self):
        self.log = []
        super().__init__()

    def pool(self):
        return self.shared(lambda: Pool(self.log))

    def repository(self):
        return self.shared(lambda: Resource(self.log, "repository"))

    def make(self):
        self.pool()
        self.repository()


class SyncDatabase(PilgrimAssembly):
    def __init__(self):
        self.log = []
        super().__init__()

    def ledger(self):
        return self.shared(lambda: Resource(self.log, "ledger"))

    def repository(self):
        return self.shared(lambda: Resource(self.log, "repository"))

    def make(self):
        self.ledger()
        self.repository()


def held_with_shared(kind):
    class Holder(PilgrimAssembly):
        def database(self):
            return self.shared(kind)

    holder = Holder()
    return holder, holder.database()


def app_type(kind):
    class App(PilgrimAssembly):
        def __init__(self):
            self.database = AssemblyHolder.shared(kind)
            super().__init__()

    return App


def held_with_assembly_holder(kind):
    app = app_type(kind)()
    return app, app.database


def app_held_by_assembly_holder(kind):
    app = AssemblyHolder.shared(app_type(kind))
    return app, app.database


def await_close(assembly, outer):
    async def main():
        await assembly.close()

    asyncio.run(main())


def run_close(assembly, outer):
    asyncio.run(assembly.close())


def aclose(assembly, outer):
    asyncio.run(assembly.aclose())


def async_with(assembly, outer):
    async def main():
        async with assembly:
            pass

    asyncio.run(main())


def with_(assembly, outer):
    with assembly:
        pass


def outer_close(assembly, outer):
    outer.close()


def outer_aclose(assembly, outer):
    asyncio.run(outer.aclose())


def holder_close(assembly, outer):
    AssemblyHolder.close()


def holder_aclose(assembly, outer):
    asyncio.run(AssemblyHolder.aclose())


ALONE = None
SHARED = held_with_shared
OWNED = held_with_assembly_holder
HELD_APP = app_held_by_assembly_holder

# How each way shuts down an assembly that is async with something async made, async with nothing made, or sync.
# "closed": every teardown runs; "refused": AsyncTeardownRequired, and nothing is closed; exception types: Python
# raises it, since a sync assembly's close() returns None, after closing it.
MATRIX = [
    ("await close()", ALONE, await_close, "closed", "closed", TypeError),
    # asyncio.run(None) raises ValueError up to Python 3.13, and TypeError from 3.14.
    ("asyncio.run(close())", ALONE, run_close, "closed", "closed", (ValueError, TypeError)),
    ("await aclose()", ALONE, aclose, "closed", "closed", "closed"),
    ("async with", ALONE, async_with, "closed", "closed", "closed"),
    ("with", ALONE, with_, "refused", "refused", "closed"),
    ("self.shared, close()", SHARED, outer_close, "refused", "closed", "closed"),
    ("self.shared, await aclose()", SHARED, outer_aclose, "closed", "closed", "closed"),
    ("AssemblyHolder.shared, owner's close()", OWNED, outer_close, "refused", "closed", "closed"),
    ("AssemblyHolder.shared, owner's await aclose()", OWNED, outer_aclose, "closed", "closed", "closed"),
    ("AssemblyHolder.shared, AssemblyHolder.close()", HELD_APP, holder_close, "refused", "closed", "closed"),
    ("AssemblyHolder.shared, await AssemblyHolder.aclose()", HELD_APP, holder_aclose, "closed", "closed", "closed"),
]


class TestShutdownMatrix(unittest.TestCase):
    def setUp(self):
        AssemblyHolder.reset()

    def tearDown(self):
        AssemblyHolder.reset()

    def test_every_way_to_shut_down(self):
        kinds = (
            ("async, made", AsyncDatabase, True, ["repository", "pool"]),
            ("async, nothing made", AsyncDatabase, False, []),
            ("sync, made", SyncDatabase, True, ["repository", "ledger"]),
            ("sync, nothing made", SyncDatabase, False, []),
        )
        for way, hold, shut_down, *expected in MATRIX:
            for name, kind, made, log in kinds:
                outcome = expected[0 if kind is AsyncDatabase and made else 1 if kind is AsyncDatabase else 2]
                with self.subTest(way=way, assembly=name):
                    AssemblyHolder.reset()
                    outer, assembly = (None, kind()) if hold is None else hold(kind)
                    if made:
                        assembly.make()
                    with warnings.catch_warnings():
                        warnings.simplefilter("error")
                        if outcome == "closed":
                            shut_down(assembly, outer)
                        elif outcome == "refused":
                            with self.assertRaisesRegex(AsyncTeardownRequired, "await aclose()"):
                                shut_down(assembly, outer)
                        else:
                            with self.assertRaises(outcome):
                                shut_down(assembly, outer)
                        gc.collect()
                    if outcome == "refused":
                        self.assertEqual(assembly.log, [])
                        self.assertFalse(assembly._closed)
                        asyncio.run((outer or assembly).aclose())
                    self.assertEqual(assembly.log, log)
                    self.assertTrue(assembly._closed)
                    if outer is not None:
                        self.assertTrue(outer._closed)


class TestAsyncPilgrimAssembly(unittest.TestCase):
    def test_close_is_a_native_coroutine_that_closes_it(self):
        assembly = AsyncDatabase()
        assembly.make()
        closing = assembly.close()
        self.assertTrue(asyncio.iscoroutine(closing))
        self.assertFalse(assembly._closed)
        asyncio.run(closing)
        self.assertEqual(assembly.log, ["repository", "pool"])

    def test_close_left_unawaited_warns_and_closes_nothing(self):
        assembly = AsyncDatabase()
        assembly.make()
        with self.assertWarnsRegex(RuntimeWarning, "never awaited"):
            assembly.close()
            gc.collect()
        self.assertEqual(assembly.log, [])
        self.assertFalse(assembly._closed)
        asyncio.run(assembly.aclose())

    def test_closing_twice_closes_once(self):
        assembly = AsyncDatabase()
        assembly.make()
        asyncio.run(assembly.close())
        asyncio.run(assembly.close())
        asyncio.run(assembly.aclose())
        self.assertEqual(assembly.log, ["repository", "pool"])

    def test_with_refuses_on_entering_so_its_body_doesnt_run(self):
        assembly = AsyncDatabase()
        ran = False
        with self.assertRaisesRegex(AsyncTeardownRequired, "AsyncDatabase can't be used with `with`"):
            with assembly:
                ran = True
        self.assertFalse(ran)
        self.assertFalse(assembly._closed)

    def test_exit_without_enter_closes_in_sync_or_refuses(self):
        # ExitStack.push registers __exit__ without calling __enter__.
        empty = AsyncDatabase()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with contextlib.ExitStack() as stack:
                stack.push(empty)
        self.assertTrue(empty._closed)

        made = AsyncDatabase()
        made.make()
        with self.assertRaisesRegex(AsyncTeardownRequired, REFUSAL):
            with contextlib.ExitStack() as stack:
                stack.push(made)
        self.assertEqual(made.log, [])
        self.assertFalse(made._closed)
        asyncio.run(made.aclose())

    def test_a_sync_assemblys_close_still_returns_none(self):
        assembly = SyncDatabase()
        assembly.make()
        self.assertIsNone(assembly.close())
        self.assertEqual(assembly.log, ["repository", "ledger"])

    def test_is_a_pilgrim_assembly(self):
        self.assertIsInstance(AsyncDatabase(), PilgrimAssembly)

    def test_a_subclass_of_a_subclass_that_overrides_aclose_still_closes_its_components_newest_first(self):
        expected = ["ClosingDatabase.aclose", "Driver.aclose", "cache", "repository", "pool"]
        for name, shut_down in (("await close()", await_close), ("holder's await aclose()", outer_aclose)):
            with self.subTest(name):
                holder, assembly = held_with_shared(ClosingDatabase)
                assembly.make()
                assembly.cache()
                shut_down(assembly, holder)
                self.assertEqual(assembly.log, expected)

    def test_a_sync_close_refuses_an_overridden_aclose_even_with_nothing_async_made(self):
        for name, hold, shut_down in (
            ("self.shared, close()", SHARED, outer_close),
            ("AssemblyHolder.shared, owner's close()", OWNED, outer_close),
            ("AssemblyHolder.close()", HELD_APP, holder_close),
            ("__exit__ without __enter__", ALONE, lambda assembly, outer: assembly.__exit__(None, None, None)),
        ):
            with self.subTest(name):
                AssemblyHolder.reset()
                outer, assembly = (None, ClosingDatabase()) if hold is None else hold(ClosingDatabase)
                assembly.repository()
                with self.assertRaisesRegex(AsyncTeardownRequired, r"ClosingDatabase\.aclose\(\) is async"):
                    shut_down(assembly, outer)
                self.assertEqual(assembly.log, [])
                self.assertFalse(assembly._closed)
                asyncio.run((outer or assembly).aclose())
                self.assertEqual(assembly.log, ["ClosingDatabase.aclose", "Driver.aclose", "repository"])
        AssemblyHolder.reset()

    def test_a_sync_close_refuses_an_aclose_override_inherited_from_a_mixin(self):
        class Flushing:
            async def aclose(self):
                self.log.append("Flushing.aclose")
                await super().aclose()

        class Flushed(Flushing, AsyncDatabase):
            pass

        class NotOverridden(AsyncDatabase, Flushing):
            pass

        holder, assembly = held_with_shared(Flushed)
        assembly.repository()
        with self.assertRaisesRegex(AsyncTeardownRequired, r"Flushed\.aclose\(\) is async"):
            holder.close()
        self.assertEqual(assembly.log, [])
        asyncio.run(holder.aclose())
        self.assertEqual(assembly.log, ["Flushing.aclose", "repository"])

        # PilgrimAssembly.aclose comes first in its MRO, so the mixin's never runs, and a sync close is fine.
        holder, assembly = held_with_shared(NotOverridden)
        assembly.repository()
        holder.close()
        self.assertEqual(assembly.log, ["repository"])


class Driver(AsyncDatabase):
    """
    An async assembly that overrides aclose(), to close something it doesn't hold as a component.
    """

    async def aclose(self):
        self.log.append("Driver.aclose")
        await super().aclose()


class ClosingDatabase(Driver):
    def cache(self):
        return self.shared(lambda: Resource(self.log, "cache"))

    async def aclose(self):
        self.log.append("ClosingDatabase.aclose")
        await super().aclose()


class Inner(PilgrimAssembly):
    """
    An assembly that overrides close() as async def, as in liberation-data/pi-pilgrim#9.
    """

    def __init__(self):
        self.log = []
        super().__init__()

    async def close(self):
        await self.aclose()

    def resource(self):
        return self.shared(lambda: Resource(self.log, "inner"))


class AwaitableClose(PilgrimAssembly):
    """
    An assembly whose sync close() closes it and returns an awaitable, as drivine-python 0.3.1's does.
    """

    def __init__(self):
        self.log = []
        super().__init__()

    def close(self):  # type: ignore[override]
        super().close()
        return asyncio.sleep(0)

    def resource(self):
        return self.shared(lambda: Resource(self.log, "awaitable close"))


class TestAnAssemblyIsClosedThroughItsOwnLifecycle(unittest.TestCase):
    def setUp(self):
        AssemblyHolder.reset()

    def tearDown(self):
        AssemblyHolder.reset()

    def test_an_owner_closes_an_owned_assembly_whose_close_is_async(self):
        class Owner(PilgrimAssembly):
            def __init__(self):
                self.inner = AssemblyHolder.shared(Inner)
                super().__init__()

        owner = Owner()
        owner.inner.resource()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            owner.close()
            gc.collect()
        self.assertTrue(owner.inner._closed)
        self.assertEqual(owner.inner.log, ["inner"])

    def test_a_holder_closes_a_component_assembly_whatever_its_close_returns(self):
        for kind in (Inner, AwaitableClose, AsyncDatabase):
            with self.subTest(kind.__name__):
                holder, assembly = held_with_shared(kind)
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    holder.close()
                    gc.collect()
                self.assertTrue(assembly._closed)

    def test_a_holder_closes_a_component_assembly_with_sync_teardowns(self):
        for kind in (Inner, AwaitableClose):
            with self.subTest(kind.__name__):
                holder, assembly = held_with_shared(kind)
                assembly.resource()
                with warnings.catch_warnings():
                    warnings.simplefilter("error")
                    holder.close()
                    gc.collect()
                self.assertEqual(assembly.log, ["inner" if kind is Inner else "awaitable close"])

    def test_assembly_holder_close_closes_a_held_async_assembly_with_sync_teardowns(self):
        database = AssemblyHolder.shared(AsyncDatabase)
        database.repository()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            AssemblyHolder.close()
            gc.collect()
        self.assertTrue(database._closed)
        self.assertEqual(database.log, ["repository"])

    def test_aclose_awaits_a_component_assemblys_aclose(self):
        holder, assembly = held_with_shared(AsyncDatabase)
        assembly.make()
        calls = []
        original = assembly.aclose

        async def aclose():
            calls.append("aclose")
            await original()

        assembly.aclose = aclose
        asyncio.run(holder.aclose())
        self.assertEqual(calls, ["aclose"])
        self.assertEqual(assembly.log, ["repository", "pool"])

    def test_a_component_assemblys_failed_teardown_doesnt_stop_the_others(self):
        class Failing:
            def __init__(self, log):
                self.log = log

            def close(self):
                self.log.append("failing")
                raise RuntimeError("failed")

        for kind in (SyncDatabase, AsyncDatabase):
            for name, close in (("close", lambda it: it.close()), ("aclose", lambda it: asyncio.run(it.aclose()))):
                with self.subTest(kind.__name__, close=name):
                    log = []

                    class Database(kind):
                        def failing(self):
                            return self.shared(lambda: Failing(self.log))

                    class Holder(PilgrimAssembly):
                        def before(self):
                            return self.shared(lambda: Resource(log, "before"))

                        def database(self):
                            return self.shared(Database)

                        def after(self):
                            return self.shared(lambda: Resource(log, "after"))

                    holder = Holder()
                    holder.before()
                    database = holder.database()
                    database.failing()
                    database.repository()
                    holder.after()
                    with self.assertRaises(ExceptionGroup) as caught:
                        close(holder)
                    self.assertEqual(log, ["after", "before"])
                    self.assertEqual(database.log, ["repository", "failing"])
                    (group,) = caught.exception.exceptions
                    self.assertIsInstance(group, ExceptionGroup)
                    self.assertEqual(len(group.__notes__), 1)
                    self.assertIn("database", group.__notes__[0])
                    self.assertEqual(str(group.exceptions[0]), "failed")

    def test_a_component_assemblys_destroy_hook_is_still_used(self):
        log = []

        class Holder(PilgrimAssembly):
            def database(self):
                return self.shared(SyncDatabase, destroy=lambda it: log.append("destroyed"))

        holder = Holder()
        database = holder.database()
        database.make()
        holder.close()
        self.assertEqual(log, ["destroyed"])
        self.assertFalse(database._closed)

    def test_the_generic_refusal_still_holds_for_a_component_that_isnt_an_assembly(self):
        class Component:
            def close(self):
                return asyncio.sleep(0)

        class Holder(PilgrimAssembly):
            def component(self):
                return self.shared(Component)

        holder = Holder()
        holder.component()
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with self.assertRaises(ExceptionGroup) as caught:
                holder.close()
            gc.collect()
        self.assertIsInstance(caught.exception.exceptions[0], AsyncTeardownRequired)
        self.assertIn("returned an awaitable", str(caught.exception.exceptions[0]))


class AsyncComponents(PilgrimAssembly):
    """
    A plain PilgrimAssembly with an async teardown: its close() refuses, as before.
    """

    def pool(self):
        return self.shared(lambda: Pool([]))


class TestAsyncTeardownRequired(unittest.TestCase):
    def setUp(self):
        AssemblyHolder.reset()

    def tearDown(self):
        AssemblyHolder.reset()

    def assertRequired(self, close, text=REFUSAL):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with self.assertRaises(AsyncTeardownRequired) as caught:
                close()
            gc.collect()
        self.assertIsInstance(caught.exception, PilgrimError)
        self.assertIn(text, str(caught.exception))

    def test_is_a_pilgrim_error(self):
        self.assertTrue(issubclass(AsyncTeardownRequired, PilgrimError))

    def test_close_of_an_assembly_with_an_async_teardown(self):
        assembly = AsyncComponents()
        assembly.pool()
        self.assertRequired(assembly.close)
        asyncio.run(assembly.aclose())

    def test_with_on_an_assembly_with_an_async_teardown(self):
        assembly = AsyncComponents()
        assembly.pool()

        def close():
            with assembly:
                pass

        self.assertRequired(close)
        asyncio.run(assembly.aclose())

    def test_close_of_an_owner_whose_owned_assembly_has_an_async_teardown(self):
        class Owner(PilgrimAssembly):
            def __init__(self):
                self.database = AssemblyHolder.shared(AsyncDatabase)
                super().__init__()

        owner = Owner()
        owner.database.make()
        self.assertRequired(owner.close)
        asyncio.run(owner.aclose())

    def test_close_of_a_holder_whose_component_assembly_has_an_async_teardown(self):
        holder, assembly = held_with_shared(AsyncDatabase)
        assembly.make()
        self.assertRequired(holder.close)
        asyncio.run(holder.aclose())

    def test_assembly_holder_close(self):
        AssemblyHolder.shared(AsyncDatabase).make()
        self.assertRequired(AssemblyHolder.close)
        asyncio.run(AssemblyHolder.aclose())

    def test_with_on_an_async_pilgrim_assembly(self):
        def close():
            with AsyncDatabase():
                pass

        self.assertRequired(close, "Use async with, or close it with await aclose().")

    def test_a_teardown_that_returns_an_awaitable(self):
        assembly = PilgrimAssembly()
        assembly.shared(lambda: object(), destroy=lambda it: asyncio.sleep(0))
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            with self.assertRaises(ExceptionGroup) as caught:
                assembly.close()
            gc.collect()
        (error,) = caught.exception.exceptions
        self.assertIsInstance(error, AsyncTeardownRequired)
        self.assertIn("Close PilgrimAssembly with await aclose().", str(error))

    def test_any_other_refusal_is_a_plain_pilgrim_error(self):
        class Closer(PilgrimAssembly):
            def closer(self):
                return self.shared(self.close)

        with self.assertRaises(PilgrimError) as caught:
            Closer().closer()
        self.assertNotIsInstance(caught.exception, AsyncTeardownRequired)


if __name__ == "__main__":
    unittest.main()
