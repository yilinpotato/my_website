const members = {
  tomori: {
    index: "01",
    name: "高松燈",
    jp: "高松 燈",
    roman: "Tomori Takamatsu",
    role: "Vo.",
    position: "主唱",
    cv: "羊宫妃那",
    keywords: "收集、天文、直率的歌",
    summary:
      "羽丘女子学园高中一年级。感受事物的方式与周围人有些不同，不善于表达，却会用近乎呐喊的歌声把内心交出去。",
    image: "assets/user/tomori.jpg",
    link: "https://anime.bang-dream.com/mygo/character/tomori/",
    color: "oklch(0.68 0.09 265)",
  },
  anon: {
    index: "02",
    name: "千早爱音",
    jp: "千早 愛音",
    roman: "Anon Chihaya",
    role: "Gt.",
    position: "节奏吉他",
    cv: "立石凛",
    keywords: "行动力、社交、从零开始",
    summary:
      "羽丘女子学园高中一年级。转学后为了尽快融入班级而提出组建乐队。看起来擅长社交，真正面对失败时却也会动摇。",
    image: "assets/user/anon.jpg",
    link: "https://anime.bang-dream.com/mygo/character/anon/",
    color: "oklch(0.78 0.12 350)",
  },
  rana: {
    index: "03",
    name: "要乐奈",
    jp: "要 楽奈",
    roman: "Rāna Kaname",
    role: "Gt.",
    position: "主音吉他",
    cv: "青木阳菜",
    keywords: "自由、抹茶、天才吉他",
    summary:
      "花咲川女子学园初中三年级。经常神出鬼没地出现在 Live House RiNG，凭直觉判断“有趣的女人”，并用吉他表达喜恶。",
    image: "assets/user/rana.jpg",
    link: "https://anime.bang-dream.com/mygo/character/rana/",
    color: "oklch(0.78 0.12 150)",
  },
  soyo: {
    index: "04",
    name: "长崎爽世",
    jp: "長崎 そよ",
    roman: "Soyo Nagasaki",
    role: "Ba.",
    position: "贝斯",
    cv: "小日向美香",
    keywords: "温柔、低音提琴、执念",
    summary:
      "月之森女子学园高中一年级，在吹奏乐部负责低音提琴。总以温和可靠的姿态出现，内心却紧紧抓着已经失去的过去。",
    image: "assets/user/soyo.jpg",
    link: "https://anime.bang-dream.com/mygo/character/soyo/",
    color: "oklch(0.77 0.09 76)",
  },
  taki: {
    index: "05",
    name: "椎名立希",
    jp: "椎名 立希",
    roman: "Taki Shiina",
    role: "Dr.",
    position: "鼓手",
    cv: "林鼓子",
    keywords: "急躁、编曲、保护欲",
    summary:
      "花咲川女子学园高中一年级。喜欢独处，语气直接而尖锐。她非常珍视燈的歌，也因此常常把焦虑变成推动乐队前进的力量。",
    image: "assets/user/taki.jpg",
    link: "https://anime.bang-dream.com/mygo/character/taki/",
    color: "oklch(0.62 0.11 25)",
  },
};

const memberStage = document.querySelector(".member-stage");
const memberTabs = [...document.querySelectorAll(".member-tab")];
const memberPortrait = document.querySelector("#member-portrait");
const memberIndex = document.querySelector(".member-index");
const memberName = document.querySelector("#member-name");
const memberJp = document.querySelector("#member-jp");
const memberRoman = document.querySelector("#member-roman");
const memberRole = document.querySelector("#member-role");
const memberPosition = document.querySelector("#member-position");
const memberCv = document.querySelector("#member-cv");
const memberKeywords = document.querySelector("#member-keywords");
const memberSummary = document.querySelector("#member-summary");
const memberLink = document.querySelector("#member-link");

function selectMember(key, shouldFocus = false) {
  const member = members[key];
  const activeTab = memberTabs.find((tab) => tab.dataset.member === key);

  if (!member || !activeTab || activeTab.classList.contains("is-active")) {
    return;
  }

  memberStage.classList.add("is-changing");

  window.setTimeout(() => {
    document.documentElement.style.setProperty("--member-color", member.color);
    memberPortrait.src = member.image;
    memberPortrait.alt = `${member.name}角色图`;
    memberIndex.textContent = member.index;
    memberName.textContent = member.name;
    memberJp.textContent = member.jp;
    memberRoman.textContent = member.roman;
    memberRole.textContent = member.role;
    memberPosition.textContent = member.position;
    memberCv.textContent = member.cv;
    memberKeywords.textContent = member.keywords;
    memberSummary.textContent = member.summary;
    memberLink.href = member.link;

    memberTabs.forEach((tab) => {
      const isActive = tab === activeTab;
      tab.classList.toggle("is-active", isActive);
      tab.setAttribute("aria-selected", String(isActive));
      tab.tabIndex = isActive ? 0 : -1;
    });

    memberStage.classList.remove("is-changing");

    if (shouldFocus) {
      activeTab.focus();
    }
  }, 180);
}

memberTabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectMember(tab.dataset.member));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }

    event.preventDefault();
    let nextIndex = index;

    if (event.key === "ArrowLeft") nextIndex = (index - 1 + memberTabs.length) % memberTabs.length;
    if (event.key === "ArrowRight") nextIndex = (index + 1) % memberTabs.length;
    if (event.key === "Home") nextIndex = 0;
    if (event.key === "End") nextIndex = memberTabs.length - 1;

    selectMember(memberTabs[nextIndex].dataset.member, true);
  });
});

const header = document.querySelector(".site-header");
const hero = document.querySelector(".hero");
const navToggle = document.querySelector(".nav-toggle");
const siteNav = document.querySelector(".site-nav");

const headerObserver = new IntersectionObserver(
  ([entry]) => header.classList.toggle("is-scrolled", !entry.isIntersecting),
  { rootMargin: "-70px 0px 0px 0px", threshold: 0 },
);
headerObserver.observe(hero);

navToggle.addEventListener("click", () => {
  const isOpen = siteNav.classList.toggle("is-open");
  navToggle.setAttribute("aria-expanded", String(isOpen));
});

siteNav.addEventListener("click", (event) => {
  if (event.target.matches("a")) {
    siteNav.classList.remove("is-open");
    navToggle.setAttribute("aria-expanded", "false");
  }
});

const revealItems = document.querySelectorAll(".reveal");
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

if (reducedMotion || !("IntersectionObserver" in window)) {
  revealItems.forEach((item) => item.classList.add("is-visible"));
} else {
  const revealObserver = new IntersectionObserver(
    (entries, observer) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("is-visible");
          observer.unobserve(entry.target);
        }
      });
    },
    { threshold: 0.14 },
  );

  revealItems.forEach((item) => revealObserver.observe(item));
}

const lightbox = document.querySelector("#lightbox");
const lightboxImage = lightbox.querySelector("img");
const lightboxClose = lightbox.querySelector(".lightbox-close");

document.querySelectorAll(".still, .moment").forEach((button) => {
  button.addEventListener("click", () => {
    const preview = button.querySelector("img");
    lightboxImage.src = button.dataset.full;
    lightboxImage.alt = preview.alt;
    lightbox.showModal();
  });
});

lightboxClose.addEventListener("click", () => lightbox.close());
lightbox.addEventListener("click", (event) => {
  if (event.target === lightbox) {
    lightbox.close();
  }
});

const videoPlayer = document.querySelector("#video-player");
const videoExternal = document.querySelector("#video-external");
const videoItems = [...document.querySelectorAll(".video-item")];

videoItems.forEach((item) => {
  item.addEventListener("click", () => {
    if (item.classList.contains("is-active")) {
      return;
    }

    const bvid = item.dataset.bvid;
    videoPlayer.src = `https://player.bilibili.com/player.html?bvid=${bvid}&page=1&high_quality=1&danmaku=0&autoplay=1`;
    videoExternal.href = `https://www.bilibili.com/video/${bvid}/`;

    videoItems.forEach((candidate) => {
      const isActive = candidate === item;
      candidate.classList.toggle("is-active", isActive);
      candidate.setAttribute("aria-pressed", String(isActive));
    });
  });
});

const audioPlayer = document.querySelector("#audio-player");
const localTracks = [...document.querySelectorAll(".local-track")];
const playToggle = document.querySelector("#play-toggle");
const previousTrack = document.querySelector("#previous-track");
const nextTrack = document.querySelector("#next-track");
const audioProgress = document.querySelector("#audio-progress");
const audioVolume = document.querySelector("#audio-volume");
const currentTime = document.querySelector("#current-time");
const duration = document.querySelector("#duration");
const trackTitle = document.querySelector("#track-title");
const trackIndex = document.querySelector("#track-index");
const trackNote = document.querySelector("#track-note");
let activeTrackIndex = 0;

function formatTime(value) {
  if (!Number.isFinite(value)) return "--:--";
  const minutes = Math.floor(value / 60);
  const seconds = Math.floor(value % 60).toString().padStart(2, "0");
  return `${minutes}:${seconds}`;
}

function updatePlayButton() {
  const isPlaying = !audioPlayer.paused;
  playToggle.textContent = isPlaying ? "暂停" : "播放";
  playToggle.setAttribute("aria-label", isPlaying ? "暂停当前歌曲" : "播放当前歌曲");
}

function selectTrack(index, shouldPlay = true) {
  activeTrackIndex = (index + localTracks.length) % localTracks.length;
  const track = localTracks[activeTrackIndex];

  audioPlayer.src = track.dataset.src;
  trackTitle.textContent = track.dataset.title;
  trackNote.textContent = track.dataset.note;
  trackIndex.textContent = `${String(activeTrackIndex + 1).padStart(2, "0")} / ${localTracks.length}`;
  audioProgress.value = 0;
  currentTime.textContent = "0:00";
  duration.textContent = "--:--";

  localTracks.forEach((candidate, candidateIndex) => {
    const isActive = candidateIndex === activeTrackIndex;
    candidate.classList.toggle("is-active", isActive);
    candidate.setAttribute("aria-current", isActive ? "true" : "false");
  });

  if (shouldPlay) {
    audioPlayer.play().catch(() => updatePlayButton());
  }
}

localTracks.forEach((track, index) => {
  track.addEventListener("click", () => selectTrack(index));
});

playToggle.addEventListener("click", () => {
  if (audioPlayer.paused) {
    audioPlayer.play().catch(() => updatePlayButton());
  } else {
    audioPlayer.pause();
  }
});

previousTrack.addEventListener("click", () => selectTrack(activeTrackIndex - 1));
nextTrack.addEventListener("click", () => selectTrack(activeTrackIndex + 1));

audioPlayer.addEventListener("loadedmetadata", () => {
  duration.textContent = formatTime(audioPlayer.duration);
});

audioPlayer.addEventListener("timeupdate", () => {
  currentTime.textContent = formatTime(audioPlayer.currentTime);
  audioProgress.value = audioPlayer.duration
    ? String((audioPlayer.currentTime / audioPlayer.duration) * 100)
    : "0";
});

audioPlayer.addEventListener("play", updatePlayButton);
audioPlayer.addEventListener("pause", updatePlayButton);
audioPlayer.addEventListener("ended", () => selectTrack(activeTrackIndex + 1));

audioProgress.addEventListener("input", () => {
  if (audioPlayer.duration) {
    audioPlayer.currentTime = (Number(audioProgress.value) / 100) * audioPlayer.duration;
  }
});

audioVolume.addEventListener("input", () => {
  audioPlayer.volume = Number(audioVolume.value);
});

audioPlayer.volume = Number(audioVolume.value);
