# Connector doctor: `amazon_campus`
_Generated 2026-09-18 12:53 UTC_

**Symptom:** broken: sample 0 in 3s

**Likely cause (high confidence):** DuckDuckGo HTML search expects a POST request with form data rather than a GET request with query parameters.

**Evidence:** The HTML response shows the homepage search form where method='post' and action='/html/', but the connector uses self.get_text(SEARCH, params=...) which performs a GET request.

## What the endpoint returns now
`https://html.duckduckgo.com/html/` -> HTTP 200

```
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">

<!--[if IE 6]><html class="ie6" xmlns="http://www.w3.org/1999/xhtml"><![endif]-->
<!--[if IE 7]><html class="lt-ie8 lt-ie9" xmlns="http://www.w3.org/1999/xhtml"><![endif]-->
<!--[if IE 8]><html class="lt-ie9" xmlns="http://www.w3.org/1999/xhtml"><![endif]-->
<!--[if gt IE 8]><!--><html xmlns="http://www.w3.org/1999/xhtml"><!--<![endif]-->
<head>
  <link rel="canonical" href="https://duckduckgo.com/" />
  <meta http-equiv="content-type" content="text/html; charset=UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=3.0, user-scalable=1" />
  <meta name="referrer" content="origin" />
  <title>DuckDuckGo HTML: Private Search Without JavaScript</title>
  <meta name="description" content="DuckDuckGo private search for people who keep JavaScript off in their browser. It doesn't spy on your searches." />
  <link title="DuckDuckGo (HTML)" type="application/opensearchdescription+xml" rel="search" href="//duckduckgo.com/opensearch_html_v2.xml" />
  <link rel="icon" href="//duckduckgo.com/favicon.ico" type="image/x-icon" />
  <link id="icon60" rel="apple-touch-icon" href="//duckduckgo.com/assets/icons/meta/DDG-iOS-icon_60x60.png?v=2"/>
  <link id="icon76" rel="apple-touch-icon" sizes="76x76" href="//duckduckgo.com/assets/icons/meta/DDG-iOS-icon_76x76.png?v=2"/>
  <link id="icon120" rel="apple-touch-icon" sizes="120x120" hr
```

## Suggested patch

```diff
--- a/app/connectors/amazon_campus.py
+++ b/app/connectors/amazon_campus.py
@@ -76,3 +76,3 @@
             try:
-                page = await self.get_text(
-                    SEARCH, params={"q": query},
+                page = await self.post_text(
+                    SEARCH, data={"q": query},
                     headers={"User-Agent": BROWSER_UA, "Accept": "text/html"},
```

**How to verify:** pytest backend/app/connectors/amazon_campus.py

---

Reviewed by a human before merging - this is a model's reading of a page, not a test result.