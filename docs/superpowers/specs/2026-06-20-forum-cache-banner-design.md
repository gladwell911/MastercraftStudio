# Forum Cache Banner Design

**Goal**

Reduce startup friction in the native forum experience by only showing the cached-data banner when the app is actually stuck on cached content after a refresh failure.

**Problem**

The current forum startup flow returns cached data immediately whenever a cache entry exists. `ForumHomeScreen`, `ForumCategoryScreen`, `ForumNotificationsScreen`, and `ForumMessagesScreen` treat any cached response as a reason to show the `forum_cached_data` banner before the background refresh finishes. This makes normal startup look degraded even when fresh forum data loads successfully moments later.

**Chosen Approach**

Keep the existing cache-first data flow so startup still has an offline fallback, but stop surfacing the cached-data banner during the initial cached render. The UI should only show that banner after a background or manual refresh fails and the screen is still displaying previously cached content.

**Behavior Changes**

1. Loading forum data from cache should continue to populate the screen immediately.
2. Initial cached renders should not set `showingCachedData` to `true`.
3. Background refresh should still start automatically after a cached load.
4. If the refresh succeeds, no status banner should ever appear.
5. If the refresh fails and the screen still has cached content to show, set:
   - `showingCachedData: true`
   - `refreshError: error`
6. The existing `forum_refresh_failed` banner remains the visible message when cached data is being shown because the current status banner prefers refresh errors over `forum_cached_data`.

**Scope**

Apply the behavior consistently to:

- `ForumHomeScreen`
- `ForumCategoryScreen`
- `ForumNotificationsScreen`
- `ForumMessagesScreen`

Do not change cache storage format, cache freshness policy, or data-fetch ordering in this fix.

**Testing**

Add or update screen tests to cover:

- cached initial load does not immediately show the status banner
- cached initial load followed by refresh failure does show the status banner
- successful refresh after cached load clears any cached-state banner path
