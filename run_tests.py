#!/usr/bin/env python3
"""
Comprehensive Test Runner for NSE/BSE Data Downloader
====================================================

Runs all test categories and generates detailed reports.

Usage:
    python run_tests.py                    # Run all tests
    python run_tests.py --unit             # Run only unit tests
    python run_tests.py --integration      # Run only integration tests
    python run_tests.py --cli              # Run only CLI tests
    python run_tests.py --gui              # Run only GUI tests
    python run_tests.py --performance      # Run only performance tests
    python run_tests.py --verbose          # Run with verbose output
    python run_tests.py --report           # Generate detailed HTML report
"""

import sys
import os
import unittest
import argparse
import time
from pathlib import Path
from datetime import datetime
import subprocess

# Add src directory to Python path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

# Test discovery patterns
TEST_PATTERNS = {
    'unit': 'tests/unit/test_*.py',
    'integration': 'tests/integration/test_*.py',
    'cli': 'tests/cli/test_*.py',
    'gui': 'tests/gui/test_*.py',
    'performance': 'tests/performance/test_*.py'
}

class ColoredTextTestResult(unittest.TextTestResult):
    """Enhanced test result with colored output"""

    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.success_count = 0
        self.verbosity = verbosity
        
    def addSuccess(self, test):
        super().addSuccess(test)
        self.success_count += 1
        if self.verbosity > 1:
            self.stream.write(f"\033[92m✓ {test._testMethodName}\033[0m\n")
    
    def addError(self, test, err):
        super().addError(test, err)
        if self.verbosity > 1:
            self.stream.write(f"\033[91m✗ {test._testMethodName} (ERROR)\033[0m\n")
    
    def addFailure(self, test, err):
        super().addFailure(test, err)
        if self.verbosity > 1:
            self.stream.write(f"\033[91m✗ {test._testMethodName} (FAIL)\033[0m\n")
    
    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self.verbosity > 1:
            self.stream.write(f"\033[93m⚠ {test._testMethodName} (SKIPPED)\033[0m\n")


class TestRunner:
    """Main test runner class"""
    
    def __init__(self):
        self.start_time = None
        self.results = {}
        
    def discover_tests(self, pattern):
        """Discover tests matching pattern"""
        loader = unittest.TestLoader()
        start_dir = Path(__file__).parent
        
        # Convert pattern to directory
        if pattern in TEST_PATTERNS:
            test_dir = start_dir / 'tests' / pattern
            if test_dir.exists():
                return loader.discover(str(test_dir), pattern='test_*.py')
        
        return unittest.TestSuite()
    
    def run_test_suite(self, suite, category, verbosity=1):
        """Run a test suite and collect results"""
        print(f"\n{'='*60}")
        print(f"Running {category.upper()} Tests")
        print(f"{'='*60}")
        
        runner = unittest.TextTestRunner(
            verbosity=verbosity,
            resultclass=ColoredTextTestResult,
            stream=sys.stdout
        )
        
        start_time = time.time()
        result = runner.run(suite)
        end_time = time.time()
        
        # Store results
        self.results[category] = {
            'tests_run': result.testsRun,
            'failures': len(result.failures),
            'errors': len(result.errors),
            'skipped': len(result.skipped),
            'success': getattr(result, 'success_count', result.testsRun - len(result.failures) - len(result.errors)),
            'time': end_time - start_time,
            'result': result
        }
        
        # Print summary
        self.print_category_summary(category)
        
        return result
    
    def print_category_summary(self, category):
        """Print summary for a test category"""
        result = self.results[category]
        
        print(f"\n{'-'*40}")
        print(f"{category.upper()} Test Summary:")
        print(f"  Tests Run: {result['tests_run']}")
        print(f"  \033[92mPassed: {result['success']}\033[0m")
        if result['failures'] > 0:
            print(f"  \033[91mFailed: {result['failures']}\033[0m")
        if result['errors'] > 0:
            print(f"  \033[91mErrors: {result['errors']}\033[0m")
        if result['skipped'] > 0:
            print(f"  \033[93mSkipped: {result['skipped']}\033[0m")
        print(f"  Time: {result['time']:.2f}s")
        print(f"{'-'*40}")
    
    def print_final_summary(self):
        """Print final test summary"""
        total_tests = sum(r['tests_run'] for r in self.results.values())
        total_success = sum(r['success'] for r in self.results.values())
        total_failures = sum(r['failures'] for r in self.results.values())
        total_errors = sum(r['errors'] for r in self.results.values())
        total_skipped = sum(r['skipped'] for r in self.results.values())
        total_time = sum(r['time'] for r in self.results.values())
        
        print(f"\n{'='*60}")
        print("FINAL TEST SUMMARY")
        print(f"{'='*60}")
        print(f"Total Tests Run: {total_tests}")
        print(f"\033[92mTotal Passed: {total_success}\033[0m")
        if total_failures > 0:
            print(f"\033[91mTotal Failed: {total_failures}\033[0m")
        if total_errors > 0:
            print(f"\033[91mTotal Errors: {total_errors}\033[0m")
        if total_skipped > 0:
            print(f"\033[93mTotal Skipped: {total_skipped}\033[0m")
        print(f"Total Time: {total_time:.2f}s")
        
        # Calculate success rate
        if total_tests > 0:
            success_rate = (total_success / total_tests) * 100
            print(f"Success Rate: {success_rate:.1f}%")
            
            if success_rate == 100:
                print(f"\n\033[92m🎉 ALL TESTS PASSED! 🎉\033[0m")
            elif success_rate >= 90:
                print(f"\n\033[93m⚠ Most tests passed, but some issues found\033[0m")
            else:
                print(f"\n\033[91m❌ Significant test failures detected\033[0m")
        
        print(f"{'='*60}")
    
    def generate_html_report(self):
        """Generate HTML test report"""
        report_path = Path(__file__).parent / f"test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        
        html_content = self._generate_html_content()
        
        with open(report_path, 'w') as f:
            f.write(html_content)
        
        print(f"\n📊 HTML Report generated: {report_path}")
        return report_path
    
    def _generate_html_content(self):
        """Generate HTML content for test report"""
        total_tests = sum(r['tests_run'] for r in self.results.values())
        total_success = sum(r['success'] for r in self.results.values())
        success_rate = (total_success / total_tests * 100) if total_tests > 0 else 0
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>NSE/BSE Data Downloader - Test Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        .header {{ background: #f0f0f0; padding: 20px; border-radius: 5px; }}
        .summary {{ margin: 20px 0; }}
        .category {{ margin: 20px 0; padding: 15px; border: 1px solid #ddd; border-radius: 5px; }}
        .success {{ color: green; }}
        .failure {{ color: red; }}
        .error {{ color: red; }}
        .skipped {{ color: orange; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 8px; text-align: left; border-bottom: 1px solid #ddd; }}
        th {{ background-color: #f2f2f2; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>NSE/BSE Data Downloader - Test Report</h1>
        <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p>Success Rate: <strong>{success_rate:.1f}%</strong></p>
    </div>
    
    <div class="summary">
        <h2>Test Summary</h2>
        <table>
            <tr><th>Category</th><th>Tests</th><th>Passed</th><th>Failed</th><th>Errors</th><th>Skipped</th><th>Time (s)</th></tr>
        """
        
        for category, result in self.results.items():
            html += f"""
            <tr>
                <td>{category.title()}</td>
                <td>{result['tests_run']}</td>
                <td class="success">{result['success']}</td>
                <td class="failure">{result['failures']}</td>
                <td class="error">{result['errors']}</td>
                <td class="skipped">{result['skipped']}</td>
                <td>{result['time']:.2f}</td>
            </tr>
            """
        
        html += """
        </table>
    </div>
</body>
</html>
        """
        
        return html
    
    def run_tests(self, categories=None, verbosity=1, generate_report=False):
        """Run tests for specified categories"""
        self.start_time = time.time()
        
        if categories is None:
            categories = list(TEST_PATTERNS.keys())
        
        print(f"\033[96m🚀 Starting NSE/BSE Data Downloader Test Suite\033[0m")
        print(f"Categories to test: {', '.join(categories)}")
        
        overall_success = True
        
        for category in categories:
            suite = self.discover_tests(category)
            if suite.countTestCases() > 0:
                result = self.run_test_suite(suite, category, verbosity)
                if result.failures or result.errors:
                    overall_success = False
            else:
                print(f"\n⚠ No tests found for category: {category}")
        
        self.print_final_summary()
        
        if generate_report:
            self.generate_html_report()
        
        return overall_success


def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='Run NSE/BSE Data Downloader tests')
    parser.add_argument('--unit', action='store_true', help='Run unit tests only')
    parser.add_argument('--integration', action='store_true', help='Run integration tests only')
    parser.add_argument('--cli', action='store_true', help='Run CLI tests only')
    parser.add_argument('--gui', action='store_true', help='Run GUI tests only')
    parser.add_argument('--performance', action='store_true', help='Run performance tests only')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--report', action='store_true', help='Generate HTML report')
    
    args = parser.parse_args()
    
    # Determine which categories to run
    categories = []
    if args.unit:
        categories.append('unit')
    if args.integration:
        categories.append('integration')
    if args.cli:
        categories.append('cli')
    if args.gui:
        categories.append('gui')
    if args.performance:
        categories.append('performance')
    
    # If no specific category selected, run all
    if not categories:
        categories = list(TEST_PATTERNS.keys())
    
    verbosity = 2 if args.verbose else 1
    
    # Run tests
    runner = TestRunner()
    success = runner.run_tests(categories, verbosity, args.report)
    
    # Exit with appropriate code
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
