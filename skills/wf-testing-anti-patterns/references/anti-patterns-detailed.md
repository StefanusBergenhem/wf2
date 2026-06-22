Referenced by `../SKILL.md` for context on each anti-pattern. Load only when needed.

## Anti-Pattern 1: Testing Implementation, Not Behavior

### What it looks like
```python
# BAD: Testing that a specific internal method is called
def test_user_creation():
    service = UserService()
    service.create_user("Alice")
    assert service._hash_password.call_count == 1
    assert service._save_to_db.call_count == 1
```

### Why it's bad
The test is coupled to internal implementation details. If you refactor `create_user` to use a different internal structure (combining `_hash_password` and `_save_to_db` into a single method, for example), the test breaks — even though the behavior is identical. These tests punish refactoring and provide zero confidence that the feature actually works.

### Correct approach
```python
# GOOD: Testing the observable behavior
def test_user_creation():
    service = UserService(db=test_db)
    service.create_user("Alice", password="secret123")

    user = test_db.get_user("Alice")
    assert user is not None
    assert user.verify_password("secret123") is True
```

Test what the caller cares about: after calling `create_user`, a user exists and can authenticate. How the service achieves this internally is irrelevant to the test.

---

## Anti-Pattern 2: Mocking What You Own

### What it looks like
```python
# BAD: Mocking your own repository class
def test_order_total():
    mock_repo = Mock(spec=OrderRepository)
    mock_repo.get_items.return_value = [Item(price=10), Item(price=20)]
    service = OrderService(repo=mock_repo)

    total = service.calculate_total(order_id=1)
    assert total == 30
```

### Why it's bad
You are testing that `OrderService.calculate_total` correctly sums a list — but you've mocked away the real question: does `OrderRepository.get_items` actually return the right data for a given order? The mock encodes your assumption about what the repository returns, not its actual behavior. If the repository's return type changes, this test still passes — but production breaks.

### Correct approach
```python
# GOOD: Use a real (test) database or in-memory implementation
def test_order_total():
    repo = InMemoryOrderRepository()
    repo.save_order(Order(id=1, items=[Item(price=10), Item(price=20)]))
    service = OrderService(repo=repo)

    total = service.calculate_total(order_id=1)
    assert total == 30
```

Mock at boundaries you don't own (external APIs, third-party services). For your own code, use real implementations or in-memory fakes that implement the same interface.

---

## Anti-Pattern 3: Assertions That Pass With Deleted Implementation

### What it looks like
```python
# BAD: Assertion that proves nothing
def test_process_data():
    result = process_data([1, 2, 3])
    assert result is not None
    assert isinstance(result, list)
```

### Why it's bad
Delete the implementation of `process_data` and replace it with `return []`. The test still passes. A test that passes with a trivially wrong implementation is not testing anything meaningful. It gives false confidence.

### Correct approach
```python
# GOOD: Assert on specific, meaningful values
def test_process_data_doubles_each_value():
    result = process_data([1, 2, 3])
    assert result == [2, 4, 6]
```

The litmus test: could you delete or fundamentally break the implementation and have this test still pass? If yes, the assertion is too weak.

---

## Anti-Pattern 4: Testing Private Methods

### What it looks like
```python
# BAD: Reaching into private internals
def test_parse_internal_format():
    service = DataService()
    result = service._parse_internal_format("raw data")
    assert result == {"key": "value"}
```

### Why it's bad
Private methods are implementation details. They exist to support public behavior. Testing them directly couples your test suite to the internal structure, making refactoring painful. If `_parse_internal_format` is important enough to test, it should either be a public method on a separate class or tested through the public interface that uses it.

### Correct approach
```python
# GOOD: Test through the public interface
def test_data_import():
    service = DataService()
    service.import_data("raw data")

    assert service.get_record("key") == "value"
```

If a private method has complex logic worth testing independently, that's a design signal: extract it into its own class with a public interface.

---

## Anti-Pattern 5: Snapshot Overuse

### What it looks like
```javascript
// BAD: Snapshotting everything
test('renders user profile', () => {
  const component = render(<UserProfile user={testUser} />);
  expect(component).toMatchSnapshot();
});
```

### Why it's bad
Snapshot tests are easy to write but provide weak guarantees. When they fail, the most common response is to blindly update the snapshot (`--updateSnapshot`) without reviewing the diff. They test the entire output structure, so any change (even intentional ones) causes a failure. This leads to snapshot update fatigue where real regressions get waved through.

### Correct approach
```javascript
// GOOD: Test specific behaviors
test('renders user name and email', () => {
  const { getByText } = render(<UserProfile user={testUser} />);
  expect(getByText('Alice Smith')).toBeInTheDocument();
  expect(getByText('alice@example.com')).toBeInTheDocument();
});

test('shows premium badge for premium users', () => {
  const { getByTestId } = render(<UserProfile user={premiumUser} />);
  expect(getByTestId('premium-badge')).toBeInTheDocument();
});
```

Use snapshots sparingly and only for genuinely stable structures (e.g., API response schemas, serialization formats). For UI and behavior, test specific properties.

---

## Anti-Pattern 6: Test Names That Don't Describe the Scenario

### What it looks like
```python
# BAD: Vague, meaningless names
def test_calculate():
    ...

def test_user_service():
    ...

def test_edge_case():
    ...
```

### Why it's bad
When this test fails in CI, the developer sees "test_calculate FAILED" and learns nothing. They have to read the entire test body to understand what scenario broke. Good test names are documentation: they describe what should happen, under what conditions.

### Correct approach
```python
# GOOD: Name describes the scenario and expected behavior
def test_calculate_total_with_discount_applies_percentage_to_subtotal():
    ...

def test_user_service_rejects_duplicate_email_with_conflict_error():
    ...

def test_empty_cart_returns_zero_total():
    ...
```

Follow the pattern: `test_<action>_<scenario>_<expected_result>`. When the test fails, the name alone should tell you what broke.

---

## Anti-Pattern 7: Shared Mutable State Between Tests

### What it looks like
```python
# BAD: Tests share and mutate a class-level variable
class TestOrderProcessing:
    orders = []  # Shared across all tests

    def test_add_order(self):
        self.orders.append(Order(id=1))
        assert len(self.orders) == 1

    def test_remove_order(self):
        self.orders.pop()
        assert len(self.orders) == 0  # Depends on test_add_order running first
```

### Why it's bad
Tests depend on execution order. Run them in isolation and they fail. Run them in a different order and they fail. Parallel execution is impossible. One failing test can cascade into false failures in subsequent tests, making debugging a nightmare.

### Correct approach
```python
# GOOD: Each test creates its own state
class TestOrderProcessing:
    def test_add_order(self):
        orders = []
        orders.append(Order(id=1))
        assert len(orders) == 1

    def test_remove_order(self):
        orders = [Order(id=1)]
        orders.pop()
        assert len(orders) == 0
```

Each test must set up its own state, execute, and tear down independently. Use `setUp`/`tearDown` (or `beforeEach`/`afterEach`) for common setup, but never share mutable data.

---

## Anti-Pattern 8: Only Testing the Happy Path

### What it looks like
```python
# BAD: Only testing the happy path
def test_transfer_money():
    result = transfer(from_account=a, to_account=b, amount=100)
    assert result.success is True
# No tests for: insufficient funds, invalid account, negative amount...
```

### Why it's bad
Happy paths rarely break. Error paths and boundary conditions are where the worst bugs live. A test suite with only happy-path tests provides a false sense of coverage.

### Correct approach
```python
# GOOD: Test error paths and boundaries explicitly
def test_transfer_succeeds_with_sufficient_funds():
    result = transfer(from_account=a, to_account=b, amount=100)
    assert result.success is True
    assert a.balance == 900

def test_transfer_fails_with_insufficient_funds():
    result = transfer(from_account=a, to_account=b, amount=99999)
    assert result.success is False
    assert a.balance == 1000  # unchanged

def test_transfer_rejects_negative_amount():
    with pytest.raises(ValueError, match="Amount must be positive"):
        transfer(from_account=a, to_account=b, amount=-50)
```

For every feature, ask: "How can this fail? What invalid inputs are possible? What boundary conditions exist?" Test those.

---

## Anti-Pattern 9: Copy-Paste Test Blocks

### What it looks like
```python
# BAD: Nearly identical tests with minor variations
def test_parse_csv_with_commas():
    result = parse("a,b,c")
    assert result == ["a", "b", "c"]

def test_parse_csv_with_semicolons():
    result = parse("a;b;c")
    assert result == ["a", "b", "c"]

def test_parse_csv_with_tabs():
    result = parse("a\tb\tc")
    assert result == ["a", "b", "c"]

# 15 more nearly identical tests...
```

### Why it's bad
When the test structure needs to change (e.g., `parse` now returns tuples), you have to update 18 tests. Worse, copy-paste tests often have subtle copy-paste errors (forgot to change the delimiter in test 7, so it's actually testing commas twice). They bloat the test file and make it hard to see what's actually being tested.

### Correct approach
```python
# GOOD: Parameterized tests
@pytest.mark.parametrize("input_str,delimiter,expected", [
    ("a,b,c", ",", ["a", "b", "c"]),
    ("a;b;c", ";", ["a", "b", "c"]),
    ("a\tb\tc", "\t", ["a", "b", "c"]),
    ("single", ",", ["single"]),
    ("", ",", []),
])
def test_parse_csv(input_str, delimiter, expected):
    result = parse(input_str, delimiter=delimiter)
    assert result == expected
```

Use parameterized tests for variations on the same scenario. Reserve separate test functions for genuinely different scenarios that need different setup, assertions, or documentation.

---

## Anti-Pattern 10: Coincidental Field Equality

### What it looks like

```
# BAD: two fields that can differ in production are given the same value
fixture = {
    "id":          "abc-123",
    "external_id": "abc-123",   # same as id — coincidental equality
    "name":        "Widget",
}
```

### Why it's bad

When two fields share the same value in a fixture, a buggy implementation that confuses the two fields will still pass all assertions. Common cases include: an internal primary key and an external / legacy reference ID, two timestamp fields (created_at / updated_at), two URL fields (canonical / redirect), or a primary key vs. a natural key. The test appears to cover both fields but actually verifies nothing about the distinction between them.

### Correct approach

```
# GOOD: use clearly distinct sentinel values for fields that can differ
fixture = {
    "id":          "abc-123",
    "external_id": "ext-abc-123",   # distinct — any swap surfaces immediately
    "name":        "Widget",
}
```

Use a clearly distinct value — a suffix (`-ext`, `-internal`, `-ref`) or a completely different token works well. If two fields legitimately share the same upstream concept, add a differentiating suffix anyway so that a swap in either direction causes an assertion mismatch.

**Rule:** When a fixture initializes two or more fields that may hold different values in production, each field MUST receive a distinct sentinel value. "They happen to be the same in this test" is not a valid justification.

---

## Anti-Pattern 11: Exact-Count Assertions on Shared State

### What it looks like

```
# BAD: asserting an exact row count on a table shared with other tests
def test_list_orders():
    rows = db.query("SELECT * FROM orders")
    assert len(rows) == 5      # passes today; breaks when any other test adds a row
```

### Why it's bad

Exact total counts (`COUNT(*) == N`, `len(items) == 5`, `toHaveLength(7)`) on shared state are a time bomb. The moment any other task, seed script, setup hook, or parallel test adds a row to the same store, the assertion breaks — producing a false negative that looks like a regression in your code when the real cause is unrelated activity elsewhere.

### Correct approach

```
# GOOD option A — assert a lower bound when "at least N" is the real requirement
rows = db.query("SELECT * FROM orders")
assert len(rows) >= 5

# GOOD option B — assert specific identities, not counts
ids = {row["id"] for row in rows}
assert "order-A1" in ids
assert "order-B2" in ids

# GOOD option C — use isolated state (e.g., a dedicated namespace, isolated table,
# or an in-memory store) so exact counts are stable and meaningful
```

**Rule:** NEVER assert an exact total count on state that any other test, task, or setup fixture can write to. Assert a lower bound (≥N), assert specific record identities, or move the assertion to truly isolated state.

---

## Anti-Pattern 12: Negative-Boundary Tests Without Scoping Annotation

### What it looks like

```
# BAD: bare negative assertion with no annotation
def test_report_module_does_not_call_payment_service():
    # ... assert no call to payment service
    pass
    # A later task legitimately introduces the call — this test mysteriously fails.
```

### Why it's bad

Tests that assert "X does NOT depend on / call / import / reference Y" enforce architectural boundaries that are correct at the time of writing. However, the boundary may be legitimately relaxed by a future task. When that happens, the bare negative test fails in completely unrelated work, with no trace of why the constraint existed or who has the authority to relax it.

### Correct approach

**Option A — Annotate with the enforced AC and the future task that may relax it:**

```
def test_report_module_does_not_call_payment_service():
    # Enforces AC-7: report generation must be free of payment coupling.
    # If a future task legitimately introduces this dependency, update
    # this test at the same time and record the ADR decision here.
    # ... assertion
```

**Option B — Rephrase as a positive allow-list (preferred when feasible):**

```
def test_report_module_only_calls_allowed_dependencies():
    # All outbound calls from ReportModule must be to exactly:
    # {TemplateEngine, AuditLogger, StorageAdapter}
    # Adding a new legitimate dependency requires extending this list explicitly,
    # making the change visible in code review.
    allowed = {"TemplateEngine", "AuditLogger", "StorageAdapter"}
    actual = get_outbound_calls(ReportModule)
    assert actual.issubset(allowed)
```

**Rule:** A bare negative-boundary test with no annotation is forbidden. Either (a) include an inline comment naming the AC it enforces and identifying which future task may relax it, OR (b) phrase it as a positive allow-list assertion where adding a new legitimate dependency requires explicitly extending the list. Both approaches leave a paper trail that reviewers and future task authors can follow.

---

## Anti-Pattern 13: Test Depends On State Seeded By Another Test, Migration, Or Fixture

### What it looks like

```
# BAD: integration test asserts on rows it expects the migration to have seeded
def test_lists_admin_users():
    repo = UserRepository(db)
    users = repo.list_admins()
    # passes when the test runs in isolation against a freshly-migrated DB;
    # breaks when a sibling test in the same package has truncated `users`,
    # when a parallel run reorders execution, or when the migration changes.
    assert any(u.email == "root@example.com" for u in users)
```

### Why it's bad

When a test's setup is "the migration / fixture / seed file already populated the world before I ran," the test is fragile to anything else that touches that shared state — sibling tests that truncate, parallel runs that reorder, ad-hoc seed scripts that change what gets seeded. Build sandboxes that skip the dependency (no database, no live service) compound the problem: the test "passes" by being unrunnable, then surfaces the cross-test interaction only at post-merge or e2e validation, where the defect spans tasks and is expensive to localize.

This is broader than AP#7 and AP#11. AP#7 governs what a single test does to its own state. AP#11 governs exact-count assertions on a shared store. AP#13 governs the *precondition* a test assumes about a world populated by *other* code paths it doesn't control.

### Correct approach

```
# GOOD option A — idempotent precondition helper called at test start
def test_lists_admin_users():
    ensure_admin_user(db, email="root@example.com")  # INSERT ... ON CONFLICT DO NOTHING
    repo = UserRepository(db)
    users = repo.list_admins()
    assert any(u.email == "root@example.com" for u in users)

# GOOD option B — self-contained seed with explicit cleanup
def test_lists_admin_users():
    user_id = create_admin(db, email=f"root-AP13-{uuid()}@example.com")
    addCleanup(lambda: delete_user(db, user_id))
    repo = UserRepository(db)
    users = repo.list_admins()
    assert any(u.id == user_id for u in users)

# GOOD option C — use isolated state (dedicated table, namespace, or
# transactional rollback) so external truncations cannot affect the
# assertion at all
```

**Rule:** A test that depends on shared mutable state (database rows, files on disk, environment variables, in-process registries, message-queue contents) MUST NOT assume that state has been set up by an external actor — a sibling test, a migration, a global fixture, a setup hook on another path. Either (a) call an idempotent precondition helper inside the test that seeds the required state, (b) seed self-contained data inside the test with explicit cleanup, or (c) move the assertion to genuinely isolated state. The assertion's correctness must not depend on the order in which other tests run, whether the dependency is available in this sandbox, or whether a sibling truncated the store first.

The most common failure mode: an integration test seeded by a migration row, paired with a sibling test that does `DELETE FROM <table>` (or its equivalent) as setup. The two tests pass independently. The assertion fails non-deterministically based on test ordering, and the build sandbox often hides it because the dependency is absent there. The bug surfaces post-merge or at e2e validation, where localizing it requires running the suite twice with different orderings.
