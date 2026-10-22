// GitHub PR links open in Loupe, except those Loupe itself sends to the
// browser, which it marks with ?via=loupe so they aren't sent straight back.
export default {
  defaultBrowser: "org.mozilla.firefoxdeveloperedition",
  options: {
    hideIcon: true
  },
  handlers: [
    {
      match: (url) =>
        /^(www\.)?github\.com$/.test(url.hostname) &&
        /^\/[^/]+\/[^/]+\/pull\/\d+/.test(url.pathname) &&
        url.searchParams.get("via") !== "loupe",
      browser: "com.glenmailer.Loupe",
    },
  ],
};
