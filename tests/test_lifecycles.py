import gc
import threading
import time
import unittest
import weakref

from pipilgrim import PilgrimAssembly, PilgrimConfigurable, PilgrimError
from tests.utils.threads import run_in_threads


class Engine:
    made = 0

    def __init__(self, size=None):
        Engine.made += 1
        self.size = size
        self.car = None


class EmptyBag:
    def __len__(self):
        return 0


class Clause:
    """
    Like an SQL expression: hashable, but == doesn't give a bool.
    """

    def __eq__(self, other):
        return self

    def __bool__(self):
        raise TypeError("a clause has no truth value")

    __hash__ = object.__hash__


class Car(PilgrimConfigurable):
    def __init__(self, engine):
        self.engine = engine
        self.configured_with = None

    def configure(self, assembly):
        self.configured_with = assembly
        self.engine.car = self


class GarageAssembly(PilgrimAssembly):
    def __init__(self):
        self.configuring = threading.Event()
        self.log = []
        self.links = 3
        super().__init__()

    def engine(self):
        return self.shared(lambda: Engine())

    def sized_engine(self, size):
        return self.shared(lambda: Engine(size))

    def sized_spare(self, *, size):
        return self.object_graph(lambda: Engine(size))

    def sized_shared(self, *, size):
        return self.shared(lambda: Engine(size))

    def engines(self, *sizes):
        return self.shared(lambda: Engine(sizes))

    def mixed_engine(self, *sizes, **tags):
        return self.shared(lambda: Engine((sizes, tags)))

    def tagged_engine(self, **tags):
        return self.shared(lambda: Engine(tags))

    def any_engine(self, size):
        return self.unshared(lambda: Engine(size))

    def weak_engine(self):
        return self.weak_shared(lambda: Engine())

    def weak_bag(self):
        return self.weak_shared(lambda: EmptyBag())

    def two_bags(self):
        return self.unshared(lambda: (self.weak_bag(), self.weak_bag()))

    def weak_sized(self, size):
        return self.weak_shared(lambda: Engine(size))

    def weak_number(self):
        return self.weak_shared(lambda: 42)

    def car(self):
        return self.unshared(lambda: Car(self.engine()))

    def spare_pair(self):
        def make():
            first = self.spare()
            self.configuring.set()
            time.sleep(0.05)
            return first, self.spare()

        return self.unshared(make)

    def spare(self):
        return self.object_graph(lambda: Engine())

    def slow_weak_engine(self):
        def make():
            time.sleep(0.05)
            return Engine()

        return self.weak_shared(make)

    def via_unshared(self):
        return self.shared(lambda: self.hop())

    def hop(self):
        return self.unshared(lambda: self.via_unshared())

    def named_loop(self, name):
        return self.shared(lambda: self.named_loop(name))

    def slow_engine(self):
        def make():
            time.sleep(0.05)
            return Engine()

        return self.shared(make)

    def chicken(self):
        return self.shared(lambda: ("chicken", self.egg()))

    def egg(self):
        return self.shared(lambda: ("egg", self.chicken()))

    def loop(self, size):
        return self.shared(lambda: self.loop(size))

    def countdown(self, size):
        return self.unshared(lambda: [size] + (self.countdown(size - 1) if size else []))

    def chain(self):
        def make():
            self.links -= 1
            return [self.links] + (self.chain() if self.links else [])

        return self.unshared(make)

    def nested(self, items):
        return self.unshared(lambda: [items] + (self.nested(items[1:]) if items else []))

    def renamed(self, size):
        del size
        return self.shared(lambda: Engine())

    def clauses(self, clause, more):
        return self.unshared(lambda: [clause] + (self.clauses(more, None) if more is not None else []))

    def broken(self):
        return self.object_graph(lambda: 1 / 0)

    def engine_then_broken(self):
        return self.unshared(lambda: (self.engine(), self.broken()))

    def badly_configured(self):
        return self.shared(lambda: Engine(), configure=lambda engine: 1 / 0)

    def slowly_configured(self):
        def finish(engine):
            self.configuring.set()
            time.sleep(0.05)
            engine.size = "configured"

        return self.shared(lambda: Engine(), configure=finish)

    def forgiving(self):
        def make():
            try:
                self.broken()
            except ZeroDivisionError:
                pass
            return Engine()

        return self.shared(make)

    def logged(self):
        return self.shared(lambda: Engine(), configure=lambda engine: self.log.append("logged configured"))

    def fails_after_logged(self):
        def make():
            self.logged()
            raise ValueError("no")

        return self.shared(make)

    def keeps_logged(self):
        def make():
            self.logged()
            try:
                self.broken()
            except ZeroDivisionError:
                pass
            return Engine()

        return self.shared(make)

    def interrupted(self):
        def make():
            self.engine()
            raise KeyboardInterrupt

        return self.shared(make)

    def first_logged(self):
        return self.shared(lambda: Engine(), configure=lambda engine: self.log.append("first"))

    def second_logged(self):
        return self.shared(lambda: OrderedCar(self.log), configure=lambda car: self.log.append("configure="))

    def ordered(self):
        return self.unshared(lambda: (self.first_logged(), self.second_logged()))

    def badly_configured_inner(self):
        return self.shared(lambda: Engine("inner"), configure=lambda engine: 1 / 0)

    def tolerant(self):
        def finish(engine):
            for it in (self.fails_after_logged, self.badly_configured_inner):
                try:
                    it()
                except (ValueError, ZeroDivisionError):
                    self.log.append(f"{it.__name__} failed")

        return self.shared(lambda: Engine(), configure=finish)

    def broken_after_spare(self):
        return self.unshared(lambda: (self.spare(), self.broken()))


class OrderedCar(PilgrimConfigurable):
    def __init__(self, log):
        self.log = log

    def configure(self, assembly):
        self.log.append("configurable")


class SportsGarageAssembly(GarageAssembly):
    def engine(self):
        return self.shared(lambda: ("tuned", super(SportsGarageAssembly, self).engine()))


class TestLifecycles(unittest.TestCase):
    def setUp(self):
        Engine.made = 0
        self.assembly = GarageAssembly()

    def test_arguments_name_the_instance(self):
        small = self.assembly.sized_engine(1)
        self.assertIs(small, self.assembly.sized_engine(1))
        self.assertIsNot(small, self.assembly.sized_engine(2))
        self.assertEqual(2, self.assembly.sized_engine(2).size)

    def test_keyword_only_arguments_name_the_instance(self):
        self.assertEqual(3, self.assembly.sized_spare(size=3).size)
        self.assertIs(self.assembly.sized_shared(size=3), self.assembly.sized_shared(size=3))
        self.assertIsNot(self.assembly.sized_shared(size=3), self.assembly.sized_shared(size=4))

    def test_star_arguments_name_the_instance(self):
        self.assertIs(self.assembly.engines(1, 2), self.assembly.engines(1, 2))
        self.assertEqual((2,), self.assembly.engines(2).size)
        self.assertIs(self.assembly.tagged_engine(a=1, b=2), self.assembly.tagged_engine(b=2, a=1))
        self.assertEqual({"a": 2}, self.assembly.tagged_engine(a=2).size)
        with self.assertRaisesRegex(PilgrimError, "can't be hashed"):
            self.assembly.engines([1])

    def test_a_deleted_argument_says_so(self):
        with self.assertRaisesRegex(PilgrimError, "GarageAssembly.renamed deleted one of its arguments"):
            self.assembly.renamed(1)

    def test_star_arguments_and_keywords_together_name_the_instance(self):
        self.assertIs(self.assembly.mixed_engine(1, a=2), self.assembly.mixed_engine(1, a=2))
        self.assertEqual(((1,), {"a": 2}), self.assembly.mixed_engine(1, a=2).size)
        self.assertIsNot(self.assembly.mixed_engine(1, a=2), self.assembly.mixed_engine(1, a=3))

    def test_unshared_takes_unhashable_arguments(self):
        self.assertEqual([1], self.assembly.any_engine([1]).size)

    def test_shared_refuses_unhashable_arguments(self):
        with self.assertRaisesRegex(PilgrimError, "can't be hashed"):
            self.assembly.sized_engine([1])

    def test_weak_shared_lives_while_held(self):
        held = self.assembly.weak_engine()
        self.assertIs(held, self.assembly.weak_engine())
        del held
        gc.collect()
        self.assembly.weak_engine()
        self.assertEqual(2, Engine.made)

    def test_weak_shared_is_not_kept_alive_by_the_assembly(self):
        made = weakref.ref(self.assembly.weak_engine())
        gc.collect()
        self.assertIsNone(made())
        self.assertIsInstance(self.assembly.weak_engine(), Engine)

    def test_weak_shared_forgets_released_instances(self):
        for it in range(100):
            self.assembly.weak_sized(it)
        gc.collect()
        self.assertEqual(0, len(self.assembly._weak_shared))

    def test_weak_shared_is_shared_even_when_falsy(self):
        first, second = self.assembly.two_bags()
        self.assertIs(first, second)
        self.assertIs(first, self.assembly.weak_bag())

    def test_weak_shared_refuses_what_cant_be_weakly_referenced(self):
        with self.assertRaises(PilgrimError) as caught:
            self.assembly.weak_number()
        self.assertEqual(
            "weak_shared(GarageAssembly.weak_number) made a int, which can't be weakly referenced. Use shared, or "
            "wrap it in a class.",
            str(caught.exception),
        )

    def test_configurable_is_configured_after_the_graph_is_built(self):
        car = self.assembly.car()
        self.assertIs(self.assembly, car.configured_with)
        self.assertIs(car, car.engine.car)

    def test_circular_initializers_name_the_cycle(self):
        cycle = r"shared\(GarageAssembly.chicken\) -> shared\(GarageAssembly.egg\)"
        with self.assertRaisesRegex(PilgrimError, cycle):
            self.assembly.chicken()

    def test_a_cycle_names_the_arguments(self):
        cycle = r"shared\(GarageAssembly.loop\(1\)\) -> shared\(GarageAssembly.loop\(1\)\)\."
        with self.assertRaisesRegex(PilgrimError, cycle):
            self.assembly.loop(1)

    def test_a_cycle_names_each_step_and_argument(self):
        with self.assertRaisesRegex(
            PilgrimError, r"shared\(GarageAssembly.via_unshared\) -> unshared\(GarageAssembly.hop\) -> shared"
        ):
            self.assembly.via_unshared()
        with self.assertRaisesRegex(PilgrimError, r"named_loop\('v8'\)"):
            self.assembly.named_loop("v8")

    def test_object_graph_is_let_go_after_the_request(self):
        made = weakref.ref(self.assembly.spare())
        gc.collect()
        self.assertIsNone(made())

    def test_unshared_may_recurse_into_itself(self):
        self.assertEqual([2, 1, 0], self.assembly.countdown(2))
        self.assertEqual([[1, 2], [2], []], self.assembly.nested([1, 2]))
        self.assertEqual([2, 1, 0], self.assembly.chain())

    def test_unshared_never_compares_its_arguments(self):
        first, second = Clause(), Clause()
        self.assertEqual([first, second], self.assembly.clauses(first, second))

    def test_a_failed_request_leaves_the_assembly_usable(self):
        with self.assertRaises(ZeroDivisionError):
            self.assembly.broken()
        self.assertIs(self.assembly.engine(), self.assembly.engine())
        self.assertEqual([], self.assembly._instance_stack)

    def test_a_failed_request_keeps_nothing_it_made(self):
        with self.assertRaises(ZeroDivisionError):
            self.assembly.engine_then_broken()
        self.assertEqual({}, self.assembly._shared)
        self.assembly.engine()
        self.assertEqual(2, Engine.made)

    def test_a_failed_configure_keeps_nothing(self):
        with self.assertRaises(ZeroDivisionError):
            self.assembly.badly_configured()
        self.assertEqual({}, self.assembly._shared)
        self.assertIs(self.assembly.engine(), self.assembly.engine())

    def test_a_failure_caught_in_a_factory_keeps_what_came_before(self):
        self.assembly.keeps_logged()
        self.assertEqual(["logged configured"], self.assembly.log)

    def test_an_interrupted_request_keeps_nothing(self):
        with self.assertRaises(KeyboardInterrupt):
            self.assembly.interrupted()
        self.assertEqual({}, self.assembly._made)
        self.assertEqual({}, self.assembly._shared)

    def test_configures_run_in_the_order_instances_were_made(self):
        self.assembly.ordered()
        self.assertEqual(["first", "configure=", "configurable"], self.assembly.log)

    def test_a_failed_request_leaks_no_object_graph_instance(self):
        with self.assertRaises(ZeroDivisionError):
            self.assembly.broken_after_spare()
        self.assertEqual({}, self.assembly._made)

    def test_a_failure_caught_by_a_configure_undoes_only_what_failed(self):
        engine = self.assembly.tolerant()
        self.assertEqual(["fails_after_logged failed", "badly_configured_inner failed"], self.assembly.log)
        self.assertEqual([engine], list(self.assembly._shared.values()))
        self.assertEqual([], self.assembly._pending_configures)
        self.assembly.engine()
        self.assertEqual(["fails_after_logged failed", "badly_configured_inner failed"], self.assembly.log)

    def test_a_caught_failure_leaves_the_request_going(self):
        self.assertIs(self.assembly.forgiving(), self.assembly.forgiving())
        self.assertEqual([], self.assembly._instance_stack)

    def test_no_thread_sees_a_shared_instance_before_it_is_configured(self):
        seen = []
        first = threading.Thread(target=lambda: seen.append(self.assembly.slowly_configured()))
        first.start()
        self.assembly.configuring.wait()
        seen.append(self.assembly.slowly_configured())
        first.join()
        self.assertEqual(["configured", "configured"], [it.size for it in seen])
        self.assertIs(seen[0], seen[1])

    def test_an_override_can_build_on_the_method_it_overrides(self):
        tuned, engine = SportsGarageAssembly().engine()
        self.assertEqual("tuned", tuned)
        self.assertIsInstance(engine, Engine)

    def test_shared_is_made_once_across_threads(self):
        seen = []
        run_in_threads(lambda: seen.append(self.assembly.slow_engine()), 8)
        self.assertEqual(1, Engine.made)
        self.assertTrue(all(it is seen[0] for it in seen))

    def test_weak_shared_is_made_once_across_threads(self):
        seen = []
        run_in_threads(lambda: seen.append(self.assembly.slow_weak_engine()), 8)
        self.assertEqual(1, Engine.made)
        self.assertTrue(all(it is seen[0] for it in seen))

    def test_threads_get_their_own_object_graph(self):
        pair = []
        first = threading.Thread(target=lambda: pair.extend(self.assembly.spare_pair()))
        first.start()
        self.assembly.configuring.wait()
        other = self.assembly.spare()
        first.join()
        self.assertIs(pair[0], pair[1])
        self.assertIsNot(pair[0], other)

    def test_a_shared_hit_is_fast(self):
        self.assembly.engine()
        started = time.perf_counter()
        for _ in range(10_000):
            self.assembly.engine()
        each = (time.perf_counter() - started) / 10_000
        # About 0.3 µs on an M2 Max, where inspect.stack() took about 200. The bound leaves room for slow CI.
        self.assertLess(each, 20e-6)


if __name__ == "__main__":
    unittest.main()
