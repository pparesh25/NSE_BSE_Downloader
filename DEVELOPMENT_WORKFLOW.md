# 🚀 Development Workflow Guide

## 📋 Repository Structure

```
main (production)     → v1.0.1 (stable releases only)
├── development       → v1.0.2-dev (active development)
├── feature/xyz       → specific features
└── hotfix/abc        → urgent production fixes
```

## 🎯 Version Management Strategy

### Semantic Versioning
- **Patch (1.0.x → 1.0.y)**: Bug fixes, minor improvements
- **Minor (1.x.0 → 1.y.0)**: New features, backwards compatible
- **Major (x.0.0 → y.0.0)**: Breaking changes, major overhauls

### Version Types
- **Production**: `1.0.1` (stable, tagged releases)
- **Development**: `1.0.2-dev` (active development)
- **Feature**: `1.1.0-feature-xyz` (feature branches)
- **Hotfix**: `1.0.2-hotfix-abc` (urgent fixes)

## 📝 Commit Standards

### Commit Message Format
```
🔧 Type: Brief description

✨ Detailed description of changes

Changes:
- Specific change 1
- Specific change 2
- Specific change 3

Version: x.y.z
Build Number: nn
Type: patch/minor/major

Impact: Description of user impact
```

### Commit Types
- 🚀 **Release**: Production releases
- 🔧 **Fix**: Bug fixes and patches
- ✨ **Feature**: New features
- 📝 **Docs**: Documentation updates
- 🎨 **Style**: Code formatting, UI improvements
- ⚡ **Perf**: Performance improvements
- 🧪 **Test**: Testing additions/updates
- 🔨 **Build**: Build system changes

## 🔄 Development Process

### 1. Feature Development
```bash
# Start from development branch
git checkout development
git pull origin development

# Create feature branch
git checkout -b feature/new-feature-name

# Update version.py for feature
__version__ = "1.1.0-feature-new-feature-name"
__build_number__ = [increment]

# Add to VERSION_HISTORY
"1.1.0-feature-new-feature-name": {
    "release_date": "YYYY-MM-DD",
    "features": ["Feature description"],
    "type": "feature",
    "status": "in_development"
}

# Commit changes
git add .
git commit -m "✨ Feature: New feature implementation"

# Push feature branch
git push origin feature/new-feature-name
```

### 2. Bug Fix Development
```bash
# For development fixes
git checkout development

# Update version.py
__version__ = "1.0.3-dev"
__build_number__ = [increment]

# Add to VERSION_HISTORY
"1.0.3-dev": {
    "release_date": "YYYY-MM-DD",
    "bug_fixes": ["Bug fix description"],
    "type": "patch",
    "status": "in_development"
}

# Commit
git commit -m "🔧 Fix: Bug description"
```

### 3. Production Release
```bash
# Merge development to main
git checkout main
git merge development

# Update version.py for production
__version__ = "1.0.2"  # Remove -dev suffix
"1.0.2": {
    "release_date": "YYYY-MM-DD",
    "features": [...],
    "bug_fixes": [...],
    "type": "patch",
    "status": "released"
}

# Commit and tag
git commit -m "🚀 Production Release v1.0.2"
git tag -a v1.0.2 -m "Production Release v1.0.2"

# Push to production
git push origin main
git push origin --tags

# Update development branch
git checkout development
__version__ = "1.0.3-dev"  # Next development version
```

## 📊 Version History Management

### VERSION_HISTORY Structure
```python
VERSION_HISTORY = {
    "1.0.2": {
        "release_date": "2025-08-07",
        "features": [
            "Specific feature description",
            "Another feature description"
        ],
        "bug_fixes": [
            "Specific bug fix description",
            "Another bug fix description"
        ],
        "improvements": [
            "Performance improvement description",
            "UI/UX improvement description"
        ],
        "type": "patch|minor|major",
        "status": "released|in_development",
        "breaking_changes": [
            "Breaking change description (for major versions)"
        ]
    }
}
```

## 🎯 Best Practices

### 1. Always Update Version
- Every commit must increment version appropriately
- Update `__build_number__` for each commit
- Add detailed entry in `VERSION_HISTORY`

### 2. Clear Documentation
- Describe what changed and why
- Include user impact in commit messages
- Update relevant documentation

### 3. Testing
- Test changes before committing
- Ensure all functionality works
- Verify version detection works correctly

### 4. Branch Management
- Keep main branch production-ready
- Use development for active work
- Create feature branches for major changes
- Use hotfix branches for urgent production fixes

## 🚨 Emergency Hotfix Process

```bash
# Create hotfix from main
git checkout main
git checkout -b hotfix/critical-fix

# Update version
__version__ = "1.0.2-hotfix-critical-fix"

# Fix and commit
git commit -m "🚨 Hotfix: Critical issue description"

# Merge to main
git checkout main
git merge hotfix/critical-fix

# Update version for production
__version__ = "1.0.2"

# Tag and release
git tag -a v1.0.2 -m "Hotfix Release v1.0.2"
git push origin main --tags

# Merge back to development
git checkout development
git merge main
```

## 📈 Release Checklist

- [ ] Version updated in `version.py`
- [ ] Build number incremented
- [ ] VERSION_HISTORY updated with detailed changes
- [ ] All tests passing
- [ ] Documentation updated
- [ ] Commit message follows standards
- [ ] Branch strategy followed
- [ ] Production release tagged
- [ ] Changes merged back to development

---

**Remember**: Clean history, proper versioning, and clear documentation make for professional development! 🎯
