import gc
import threading
import time
import unittest

from pipilgrim import PilgrimAssembly, PilgrimConfigurable, PilgrimError


class Engine:
    made = 0

    def __init__(self, size=None):
        Engine.made += 1
        self.size = size
        self.car = None


class Car(PilgrimConfigurable):
    def __init__(self, engine):
        self.engine = engine
        self.configured_with = None

    def configure(self, assembly):
        self.configured_with = assembly
        self.engine.car = self


class GarageAssembly(PilgrimAssembly):
    def engine(self):
        return self.shared(lambda: Engine())

    def sized_engine(self, size):
        return self.shared(lambda: Engine(size))

    def sized_spare(self, *, size):
        return self.object_graph(lambda: Engine(size))

    def any_engine(self, size):
        return self.unshared(lambda: Engine(size))

    def weak_engine(self):
        return self.weak_shared(lambda: Engine())

    def weak_number(self):
        return self.weak_shared(lambda: 42)

    def car(self):
        return self.unshared(lambda: Car(self.engine()))

    def slow_engine(self):
        def make():
            time.sleep(0.05)
            return Engine()

        return self.shared(make)

    def chicken(self):
        return self.shared(lambda: ("chicken", self.egg()))

    def egg(self):
        return self.shared(lambda: ("egg", self.chicken()))

    def broken(self):
        return self.object_graph(lambda: 1 / 0)

    def engine_then_broken(self):
        return self.unshared(lambda: (self.engine(), self.broken()))

    def badly_configured(self):
        return self.shared(lambda: Engine(), configure=lambda engine: 1 / 0)

    def slowly_configured(self):
        def finish(engine):
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
        self.assertEqual(4, self.assembly.sized_spare(size=4).size)

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

    def test_weak_shared_refuses_what_cant_be_weakly_referenced(self):
        with self.assertRaisesRegex(PilgrimError, "can't be weakly referenced"):
            self.assembly.weak_number()

    def test_configurable_is_configured_after_the_graph_is_built(self):
        car = self.assembly.car()
        self.assertIs(self.assembly, car.configured_with)
        self.assertIs(car, car.engine.car)

    def test_circular_initializers_name_the_cycle(self):
        with self.assertRaisesRegex(PilgrimError, r"shared\(GarageAssembly.chicken\) -> shared\(GarageAssembly.egg\)"):
            self.assembly.chicken()

    def test_a_failed_request_leaves_the_assembly_usable(self):
        with self.assertRaises(ZeroDivisionError):
            self.assembly.broken()
        self.assertIs(self.assembly.engine(), self.assembly.engine())
        self.assertEqual([], self.assembly._instance_stack)

    def test_a_failed_request_keeps_nothing_it_made(self):
        with self.assertRaises(ZeroDivisionError):
            self.assembly.engine_then_broken()
        self.assertEqual({}, self.assembly.shared_instances)
        self.assembly.engine()
        self.assertEqual(2, Engine.made)

    def test_a_failed_configure_keeps_nothing(self):
        with self.assertRaises(ZeroDivisionError):
            self.assembly.badly_configured()
        self.assertEqual({}, self.assembly.shared_instances)

    def test_a_caught_failure_leaves_the_request_going(self):
        self.assertIs(self.assembly.forgiving(), self.assembly.forgiving())
        self.assertEqual([], self.assembly._instance_stack)

    def test_no_thread_sees_a_shared_instance_before_it_is_configured(self):
        seen = []
        first = threading.Thread(target=lambda: seen.append(self.assembly.slowly_configured()))
        first.start()
        time.sleep(0.01)
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
        threads = [threading.Thread(target=lambda: seen.append(self.assembly.slow_engine())) for _ in range(8)]
        for it in threads:
            it.start()
        for it in threads:
            it.join()
        self.assertEqual(1, Engine.made)
        self.assertTrue(all(it is seen[0] for it in seen))

    def test_a_shared_hit_is_fast(self):
        self.assembly.engine()
        started = time.perf_counter()
        for _ in range(10_000):
            self.assembly.engine()
        each = (time.perf_counter() - started) / 10_000
        # inspect.stack() took about 200 µs here; reading the frame takes well under 5.
        self.assertLess(each, 20e-6)


if __name__ == "__main__":
    unittest.main()
