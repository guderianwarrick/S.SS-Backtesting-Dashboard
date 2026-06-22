(() => {
  const tweets = document.querySelectorAll('[data-testid="tweet"]');
  return JSON.stringify(Array.from(tweets).map(t => {
    const textEl = t.querySelector('[data-testid="tweetText"]');
    const timeEl = t.querySelector('time');
    const linkEl = t.querySelector('a[href*="/status/"]');
    return {
      text: textEl ? textEl.innerText : '',
      time: timeEl ? timeEl.getAttribute('datetime') : '',
      id: (linkEl ? linkEl.href : '').split('/status/')[1] ? (linkEl.href.split('/status/')[1].split('?')[0]) : ''
    };
  }));
})()