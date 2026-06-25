# Forum Cache Banner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop showing the native forum cached-data banner during normal startup when cached forum content is only a temporary bridge before a successful refresh.

**Architecture:** Keep the existing cache-first store behavior and narrow the fix to screen state transitions. Cached loads will still render immediately, but `showingCachedData` will only become true after a refresh failure leaves cached content on screen.

**Tech Stack:** React Native class components, Jest, react-test-renderer

---

### Task 1: Update home and category cached-load banner state

**Files:**
- Modify: `js/screens/ForumHomeScreen.js`
- Modify: `js/screens/ForumCategoryScreen.js`
- Test: `js/screens/__tests__/ForumHomeScreen.test.js`
- Test: `js/screens/__tests__/ForumCategoryScreen.test.js`

- [ ] **Step 1: Write failing tests**

Add assertions that cached initial loads do not render `forum-status-banner` until the follow-up refresh fails.

- [ ] **Step 2: Run targeted tests to verify failure**

Run: `yarn test js/screens/__tests__/ForumHomeScreen.test.js js/screens/__tests__/ForumCategoryScreen.test.js --runInBand`

- [ ] **Step 3: Write minimal implementation**

Change `loadHome()` and `loadTopics()` so cached initial loads keep `showingCachedData: false`, while existing refresh failure paths continue to set cached-data state when stale content remains visible.

- [ ] **Step 4: Run targeted tests to verify pass**

Run: `yarn test js/screens/__tests__/ForumHomeScreen.test.js js/screens/__tests__/ForumCategoryScreen.test.js --runInBand`

- [ ] **Step 5: Commit**

```bash
git add js/screens/ForumHomeScreen.js js/screens/ForumCategoryScreen.js js/screens/__tests__/ForumHomeScreen.test.js js/screens/__tests__/ForumCategoryScreen.test.js
git commit -m "fix: suppress forum cache banner on successful startup refresh"
```

### Task 2: Update notifications and messages cached-load banner state

**Files:**
- Modify: `js/screens/ForumNotificationsScreen.js`
- Modify: `js/screens/ForumMessagesScreen.js`
- Test: `js/screens/__tests__/ForumNotificationsAndMessagesScreen.test.js`

- [ ] **Step 1: Write failing tests**

Add assertions that cached notifications/messages loads do not show the banner until their follow-up refresh path fails.

- [ ] **Step 2: Run targeted tests to verify failure**

Run: `yarn test js/screens/__tests__/ForumNotificationsAndMessagesScreen.test.js --runInBand`

- [ ] **Step 3: Write minimal implementation**

Keep cached data visible on initial render, but set `showingCachedData` only from refresh-failure branches that retain previous content.

- [ ] **Step 4: Run targeted tests to verify pass**

Run: `yarn test js/screens/__tests__/ForumNotificationsAndMessagesScreen.test.js --runInBand`

- [ ] **Step 5: Commit**

```bash
git add js/screens/ForumNotificationsScreen.js js/screens/ForumMessagesScreen.js js/screens/__tests__/ForumNotificationsAndMessagesScreen.test.js
git commit -m "fix: defer forum cache banner until refresh failure"
```

### Task 3: Regression verification

**Files:**
- Test: `js/screens/__tests__/ForumHomeScreen.test.js`
- Test: `js/screens/__tests__/ForumCategoryScreen.test.js`
- Test: `js/screens/__tests__/ForumNotificationsAndMessagesScreen.test.js`

- [ ] **Step 1: Run consolidated regression coverage**

Run: `yarn test js/screens/__tests__/ForumHomeScreen.test.js js/screens/__tests__/ForumCategoryScreen.test.js js/screens/__tests__/ForumNotificationsAndMessagesScreen.test.js --runInBand`

- [ ] **Step 2: Confirm no remaining immediate cached-banner assertions**

Review the updated tests and verify they match the new product behavior: no banner on cached startup unless refresh fails.

- [ ] **Step 3: Commit final verification state if needed**

```bash
git status --short
```
