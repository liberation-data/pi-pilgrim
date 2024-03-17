class TestInstance:
    _init_count_by_name = {}

    def __init__(self, name: str, dependency: 'TestInstance' = None, required_during_init: bool = False):
        self.name = name
        self.dependency = dependency
        self.delegate = None
        self.required_during_init = required_during_init

        self._increment_init_count()

    def __del__(self):
        self._decrement_init_count()

    @property
    def init_count(self) -> int:
        return TestInstance._init_count_by_name.get(self.name, 0)

    @classmethod
    def count_for_name(cls, name: str) -> int:
        return cls._init_count_by_name.get(name, 0)

    @classmethod
    def clear_init_counts(cls):
        cls._init_count_by_name.clear()

    def _increment_init_count(self):
        if self.name in TestInstance._init_count_by_name:
            TestInstance._init_count_by_name[self.name] += 1
        else:
            TestInstance._init_count_by_name[self.name] = 1

    def _decrement_init_count(self):
        if self.name in TestInstance._init_count_by_name:
            TestInstance._init_count_by_name[self.name] -= 1
