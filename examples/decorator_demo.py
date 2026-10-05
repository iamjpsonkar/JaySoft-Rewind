"""Record a decorated function or class without middleware or a replay factory."""

from rewind import Call, LocalStore, capture


def save_failure(call: Call) -> bool:
    """Use an ordinary condition block to decide whether a call is retained."""
    if call.error is not None:
        return True
    return False


@capture(condition=save_failure)
def calculate_total(order: dict) -> int:
    return order["quantity"] * order["unit_price"]


@capture(condition=save_failure)
class Checkout:
    def __init__(self) -> None:
        self.calls = 0

    def total(self, order: dict) -> int:
        self.calls += 1
        return order["quantity"] * order["unit_price"]


def main() -> None:
    store = LocalStore()
    checkout = Checkout()
    for target, reference in (
        (calculate_total, "examples.decorator_demo:calculate_total"),
        (checkout.total, "examples.decorator_demo:Checkout.total"),
    ):
        before = set(store.ids())
        try:
            target({"quantity": 2})
        except KeyError:
            pass
        created = set(store.ids()) - before
        if len(created) != 1:
            raise RuntimeError("expected one retained failure")
        artifact = store.path / (created.pop() + ".rewind.json")
        print(f"Saved: {artifact}")
        print(f"Replay: rewind replay {artifact} --app {reference}")


if __name__ == "__main__":
    # Use the importable identity during both capture and the fresh replay worker.
    from examples.decorator_demo import main as run

    run()
