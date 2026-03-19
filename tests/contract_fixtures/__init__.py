"""Cross-repo contract fixtures.

Canonical JSON examples that both backend (Python/Pydantic) and mobile
(TypeScript) validate against.  A change in the contract shape must break
a test in *both* repos — that is the whole point.

When updating a fixture:
1. Change the JSON here.
2. Run backend tests: ``pytest tests/test_contract_alignment.py``
3. Copy the updated JSON to the mobile mirror:
   ``food-scanner-ai/src/__contract_fixtures__/``
4. Run mobile tests: ``npm test -- contract``
"""
