# Bead bf-2stnc: E2E Playwright Selector Fix

## Task
Fix E2E Playwright selector: #connect-button does not exist, actual ID is #toggle-connection

## Status: Already Fixed

The fix was already applied in commit `8665217`:
```
fix(e2e): correct connect button selector from #connect-button to #toggle-connection
```

## Verification

Current selector in `tests/e2e/test_duck_e_basic.spec.ts:20`:
```javascript
const connectButton = page.locator('#toggle-connection, button:has-text("Connect"), [data-testid="connect"]');
```

The HTML in `app/website_files/templates/chat.html:1434` confirms:
```html
<button id="toggle-connection">
```

The selector is correct and matches the actual element ID.
