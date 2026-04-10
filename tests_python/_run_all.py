import io
import sys
import unittest

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
loader = unittest.TestLoader()
suite = loader.discover('tests_python', pattern='test_*.py')
runner = unittest.TextTestRunner(verbosity=0, stream=sys.stdout)
result = runner.run(suite)
total = result.testsRun
errs = len(result.errors)
fails = len(result.failures)
print(f'Total: {total}, Errors: {errs}, Failures: {fails}')
if result.errors:
    for e in result.errors[:5]:
        name = repr(e[0])[:100]
        print(f'  ERR: {name}')
if result.failures:
    for f in result.failures[:5]:
        name = repr(f[0])[:100]
        print(f'  FAIL: {name}')
if not result.errors and not result.failures:
    print('ALL PASS')
sys.exit(0 if (errs + fails) == 0 else 1)
