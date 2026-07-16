# Contributing to ContentStudio

Thank you for your interest in contributing to ContentStudio! This document provides guidelines for contributions.

## Code of Conduct

Please be respectful and constructive in all interactions. This is an open-source community project.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/your-username/open-source-contentstudio-agent.git`
3. Create a feature branch: `git checkout -b feature/your-feature`
4. Install dependencies: `pip install -r requirements.txt`
5. Make your changes
6. Run tests: `pytest`
7. Run linters: `flake8 app/ && pylint app/ && mypy app/`
8. Commit with a clear message: `git commit -am "Brief description"`
9. Push to your fork: `git push origin feature/your-feature`
10. Open a Pull Request

## Code Quality

All contributions must:

- Pass all linters (Flake8, Pylint, Mypy, Bandit)
- Include tests for new functionality
- Maintain or improve test coverage
- Follow PEP 8 style guidelines
- Include docstrings for public functions

## Pull Request Process

1. Update README.md if you change functionality
2. Ensure CI/CD passes (GitHub Actions)
3. Request review from maintainers
4. Address review feedback
5. Squash commits if requested

## Reporting Issues

When reporting bugs, please include:

- Python version
- OS (Windows, Linux, macOS)
- Steps to reproduce
- Expected vs. actual behavior
- Error messages/logs

## Feature Requests

We welcome feature requests! Please include:

- Use case / motivation
- Proposed API (if applicable)
- Alternative approaches considered

## Development Setup

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows

# Install development dependencies
pip install -r requirements.txt
pip install pytest pytest-cov black

# Run tests
pytest

# Format code
black app/

# Run linters
flake8 app/
pylint app/
mypy app/
```

## Areas for Contribution

- Bug fixes
- Performance improvements
- Documentation improvements
- Test coverage
- New platform integrations
- Localization

## License

By contributing, you agree that your contributions will be licensed under Apache 2.0.
See the [LICENSE](./LICENSE) file for details.
