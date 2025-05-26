#!/usr/bin/env python3
"""
Test runner for the Discord Bot test suite.
Runs all tests and provides a summary report.
"""

import unittest
import sys
import os
from io import StringIO

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_all_tests():
    """Run all tests and return results."""
    # Discover and run all tests
    loader = unittest.TestLoader()
    start_dir = os.path.dirname(os.path.abspath(__file__))
    suite = loader.discover(start_dir, pattern="test_*.py")

    # Capture output
    stream = StringIO()
    runner = unittest.TextTestRunner(stream=stream, verbosity=2)
    result = runner.run(suite)

    # Print results
    output = stream.getvalue()
    print(output)

    # Print summary
    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(
        f"Skipped: {len(result.skipped) if hasattr(result, 'skipped') else 0}")

    if result.failures:
        print(f"\nFAILURES ({len(result.failures)}):")
        for test, traceback in result.failures:
            print(f"  - {test}")

    if result.errors:
        print(f"\nERRORS ({len(result.errors)}):")
        for test, traceback in result.errors:
            print(f"  - {test}")

    if result.testsRun > 0:
        success_rate = (result.testsRun - len(result.failures) -
                        len(result.errors)) / result.testsRun * 100
    else:
        success_rate = 0
    print(f"\nSuccess rate: {success_rate:.1f}%")

    if result.wasSuccessful():
        print("🎉 All tests passed!")
        return 0
    else:
        print("❌ Some tests failed.")
        return 1


def run_specific_test(test_name):
    """Run a specific test module."""
    try:
        # Import the test module
        module = __import__(f"test_{test_name}", fromlist=[""])

        # Create test suite
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(module)

        # Run tests
        runner = unittest.TextTestRunner(verbosity=2)
        result = runner.run(suite)

        return 0 if result.wasSuccessful() else 1
    except ImportError:
        print(f"Test module 'test_{test_name}' not found.")
        return 1


def main():
    """Main entry point."""
    if len(sys.argv) > 1:
        # Run specific test
        test_name = sys.argv[1]
        return run_specific_test(test_name)
    else:
        # Run all tests
        return run_all_tests()


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
