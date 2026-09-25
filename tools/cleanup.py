# -*- coding: utf-8 -*-
"""bilibilias 个人构建 patch 脚本

阶段1: 只出 arm64 + 关闭埋点
阶段2: 删 iOS 全链 / 文档层 / 多语言 / 百度统计全链
阶段3: UI 精简 —— 防伪弹窗、首页轮播图·公告·更新信息（数据源收敛）、设置·关于程序

设计要点：
  * 所有匹配基于「去掉首尾空白后的整行 / 前缀」，不依赖缩进（扫出来的代码缩进不可靠）。
  * 删多行块用括号/花括号计数，忽略字符串与 // 注释。
  * 匹配数不符立即 ::error:: 退出，绝不静默半改。
  * 每次运行都从干净 checkout 开始，因此不做幂等处理。
"""
import os
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
DONE = []


def die(m):
    print("::error::" + str(m))
    sys.exit(1)


def note(m):
    DONE.append(m)
    print("[OK] " + m)


def read(p):
    f = ROOT / p
    if not f.exists():
        die("file not found: %s" % p)
    return f, f.read_text(encoding="utf-8").split("\n")


def save(f, ls):
    f.write_text("\n".join(ls), encoding="utf-8")


def S(s):
    return s.strip()


def deltas(line):
    """返回本行净 ( 与 { 计数；忽略 // 注释与双引号字符串内部字符。"""
    p = b = 0
    inq = False
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if inq:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                inq = False
        else:
            if c == '"':
                inq = True
            elif c == "/" and i + 1 < n and line[i + 1] == "/":
                break
            elif c == "(":
                p += 1
            elif c == ")":
                p -= 1
            elif c == "{":
                b += 1
            elif c == "}":
                b -= 1
        i += 1
    return p, b


def block_end(ls, start):
    p = b = 0
    for i in range(start, len(ls)):
        dp, db = deltas(ls[i])
        p += dp
        b += db
        if i == start and p == 0 and b == 0:
            return start
        if i > start and p == 0 and b == 0:
            return i
    die("block not closed from line %d: %s" % (start + 1, ls[start]))


def find_all(ls, pred):
    return [i for i, l in enumerate(ls) if pred(S(l))]


def find_one(ls, pred, what):
    hits = find_all(ls, pred)
    if len(hits) != 1:
        die("%s: expected 1 match, got %d" % (what, len(hits)))
    return hits[0]


def assert_absent(p, token):
    f, ls = read(p)
    for i, l in enumerate(ls):
        if token in l:
            die("%s:%d still contains %r" % (p, i + 1, token))


def op_delete_lines(p, pred, expect=None, label=""):
    f, ls = read(p)
    out = []
    hit = 0
    for l in ls:
        if pred(S(l)):
            hit += 1
            continue
        out.append(l)
    if expect is not None and hit != expect:
        die("%s %s: expected %d line(s), got %d" % (p, label, expect, hit))
    save(f, out)
    note("%s: deleted %d line(s) %s" % (p, hit, label))


def op_delete_function(p, header_prefix):
    f, ls = read(p)
    i = find_one(ls, lambda s: s.startswith(header_prefix), p + " :: " + header_prefix)
    e = block_end(ls, i)
    del ls[i:e + 1]
    save(f, ls)
    note("%s: deleted function %s" % (p, header_prefix))


def op_delete_annotated_function(p, header_prefix):
    f, ls = read(p)
    i = find_one(ls, lambda s: s.startswith(header_prefix), p + " :: " + header_prefix)
    st = i
    while st > 0 and S(ls[st - 1]).startswith("@"):
        st -= 1
    e = block_end(ls, i)
    del ls[st:e + 1]
    save(f, ls)
    note("%s: deleted annotated function %s" % (p, header_prefix))


def op_stub_function(p, header_prefix, stub):
    f, ls = read(p)
    i = find_one(ls, lambda s: s.startswith(header_prefix), p + " :: " + header_prefix)
    e = block_end(ls, i)
    ls[i:e + 1] = [stub]
    save(f, ls)
    note("%s: stubbed %s" % (p, header_prefix))


def op_delete_block(p, anchor_eq, expect=None):
    f, ls = read(p)
    cnt = 0
    while True:
        hits = find_all(ls, lambda s: s == anchor_eq)
        if not hits:
            break
        e = block_end(ls, hits[0])
        del ls[hits[0]:e + 1]
        cnt += 1
    if expect is not None and cnt != expect:
        die("%s: %r expected %d block(s), got %d" % (p, anchor_eq, expect, cnt))
    save(f, ls)
    note("%s: removed %d block(s) %s" % (p, cnt, anchor_eq))


def op_delete_call(p, inner_anchor, opener):
    f, ls = read(p)
    i = find_one(ls, lambda s: s == inner_anchor, p + " :: " + inner_anchor)
    st = None
    for j in range(i, -1, -1):
        if S(ls[j]) == opener:
            st = j
            break
    if st is None:
        die("%s: %r not found above %r" % (p, opener, inner_anchor))
    e = block_end(ls, st)
    del ls[st:e + 1]
    save(f, ls)
    note("%s: removed call %s (%s)" % (p, opener, inner_anchor))


def op_replace_line(p, old_eq, new_lines):
    f, ls = read(p)
    i = find_one(ls, lambda s: s == old_eq, p + " :: " + old_eq)
    ind = ls[i][:len(ls[i]) - len(ls[i].lstrip())]
    ls[i:i + 1] = [(ind + x) if x else "" for x in new_lines]
    save(f, ls)
    note("%s: replaced %s" % (p, old_eq[:60]))


def op_sub_all(p, old, new, expect):
    f, ls = read(p)
    n = 0
    for k, l in enumerate(ls):
        c = l.count(old)
        if c:
            ls[k] = l.replace(old, new)
            n += c
    if n != expect:
        die("%s: %r found %d time(s), expected %d" % (p, old, n, expect))
    save(f, ls)
    note("%s: %r -> %r x%d" % (p, old, new, n))


def op_insert_after(p, anchor_eq, new_lines):
    f, ls = read(p)
    i = find_one(ls, lambda s: s == anchor_eq, p + " :: " + anchor_eq)
    ind = ls[i][:len(ls[i]) - len(ls[i].lstrip())]
    ls[i + 1:i + 1] = [ind + x for x in new_lines]
    save(f, ls)
    note("%s: inserted %d line(s) after %r" % (p, len(new_lines), anchor_eq[:40]))


def op_delete_enum_entries(p, func_prefix, targets):
    f, ls = read(p)
    i = find_one(ls, lambda s: s.startswith(func_prefix), p + " :: " + func_prefix)
    e = block_end(ls, i)
    n = 0
    for k in range(e, i - 1, -1):
        if S(ls[k]) in targets:
            del ls[k]
            n += 1
    if n != len(targets):
        die("%s: expected %d entries, removed %d" % (p, len(targets), n))
    save(f, ls)
    note("%s: removed %d default entries" % (p, n))


def write_file(p, text):
    (ROOT / p).write_text(text, encoding="utf-8")
    note("wrote " + p)


def rm(paths):
    for x in paths:
        q = ROOT / x
        if q.is_dir():
            shutil.rmtree(q)
            note("rm -rf " + x)
        elif q.exists():
            q.unlink()
            note("rm " + x)


def rm_locale_dirs(base, keep=("values-zh", "values-v31")):
    b = ROOT / base
    if not b.is_dir():
        return
    for d in sorted(b.iterdir()):
        if d.is_dir() and d.name.startswith("values-") and d.name not in keep:
            shutil.rmtree(d)
            note("rm -rf " + str(d.relative_to(ROOT)))


def scan(token):
    hits = []
    for dp, dn, fn in os.walk(ROOT):
        if "/.git" in dp or "/build" in dp or "/.gradle" in dp:
            continue
        for f in fn:
            if f.endswith(".kt"):
                fp = pathlib.Path(dp) / f
                try:
                    t = fp.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if token in t:
                    hits.append(str(fp.relative_to(ROOT)))
    return hits


HS = "shared/src/commonMain/kotlin/com/imcys/bilibilias/shared/feature/home/HomeScreen.kt"
HVM = "shared/src/commonMain/kotlin/com/imcys/bilibilias/shared/feature/home/HomeViewModel.kt"
SS = "shared/src/commonMain/kotlin/com/imcys/bilibilias/shared/feature/setting/SettingScreen.kt"
ASR = "core/data/src/main/java/com/imcys/bilibilias/data/repository/AppSettingsRepository.kt"
APP = "app/build.gradle.kts"
BILAPP = "app/src/main/java/com/imcys/bilibilias/BILIBILIASApplication.kt"
MAINACT = "app/src/main/java/com/imcys/bilibilias/MainActivity.kt"

# ------------------------------------------------------------------ pre-flight
print("===== pre-flight scan =====")
for tok in ("StatService", "baiduAnalyticsSafe", "packageSourceWarning"):
    print("%-22s -> %s" % (tok, scan(tok)))

# ------------------------------------------------------------------ 阶段 1
print("===== phase 1: arm64 only + analytics off =====")
op_replace_line("gradle.properties", "enabledAnalytics=true", ["enabledAnalytics=false"])
op_delete_lines("gradle.properties", lambda s: s.startswith("as.baidu.stat.id="),
                expect=1, label="drop baidu stat id")
op_replace_line(APP, 'abiFilters += listOf("arm64-v8a", "x86_64")',
                ['abiFilters += listOf("arm64-v8a")'])
op_replace_line(APP, 'include("arm64-v8a", "x86_64")', ['include("arm64-v8a")'])
op_replace_line(APP, "isUniversalApk = true", ["isUniversalApk = false"])
op_insert_after(APP, 'disable += "Instantiatable"',
                ["checkReleaseBuilds = false", "abortOnError = false"])

# ------------------------------------------------------------------ 阶段 2
print("===== phase 2: drop iOS / docs / locales / baidu =====")

# iOS: shared 的 listOf(...).forEach 块
f, ls = read("shared/build.gradle.kts")
j = find_one(ls, lambda s: s == ").forEach { iosTarget ->", "shared ios forEach")
st = None
for k in range(j, -1, -1):
    if S(ls[k]) == "listOf(":
        st = k
        break
if st is None:
    die("shared/build.gradle.kts: 'listOf(' not found above the ios forEach block")
e = block_end(ls, st)
del ls[st:e + 1]
save(f, ls)
note("shared/build.gradle.kts: removed ios targets block")

# iOS: 其余模块的裸声明
for m in ("core/common", "core/network", "core/database", "core/datastore",
          "core/data", "core/ui", "core/datastore-proto"):
    p = m + "/build.gradle.kts"
    if (ROOT / p).exists():
        op_delete_lines(p, lambda s: s in ("iosArm64()", "iosSimulatorArm64()"),
                        label="ios targets")

# 目录删除
rm(["iosApp", "docs", "fastlane", "ecology", ".github/ISSUE_TEMPLATE",
    "AGENTS.md", ".gitmodules",
    "shared/src/iosMain",
    "core/common/src/iosMain", "core/network/src/iosMain", "core/database/src/iosMain",
    "core/datastore/src/iosMain", "core/data/src/iosMain", "core/ui/src/iosMain",
    "core/datastore-proto/src/iosMain",
    "app/src/baidu", "app/libs"])
rm_locale_dirs("app/src/main/res")
rm_locale_dirs("shared/src/commonMain/composeResources")

# 百度统计：先删源码引用，再删构建侧
op_delete_block(BILAPP, "baiduAnalyticsSafe {", expect=1)
op_delete_lines(BILAPP, lambda s: s == "import com.baidu.mobstat.StatService",
                expect=1, label="drop StatService import")
op_delete_lines(BILAPP, lambda s: s == "import com.imcys.bilibilias.common.utils.baiduAnalyticsSafe",
                expect=1, label="drop baiduAnalyticsSafe import")
assert_absent(BILAPP, "StatService")

op_delete_function(MAINACT, "fun initBaiduAnalytics(")
op_delete_lines(MAINACT, lambda s: s == "initBaiduAnalytics(it.agreePrivacyPolicy)",
                expect=1, label="drop initBaiduAnalytics call")
op_delete_block(MAINACT, "baiduAnalyticsSafe {", expect=2)
op_delete_lines(MAINACT, lambda s: s == "import com.baidu.mobstat.StatService",
                expect=1, label="drop StatService import")
op_delete_lines(MAINACT, lambda s: s == "import com.imcys.bilibilias.common.utils.baiduAnalyticsSafe",
                expect=1, label="drop baiduAnalyticsSafe import")
assert_absent(MAINACT, "StatService")
assert_absent(MAINACT, "baiduAnalyticsSafe")

op_delete_lines("app/proguard-rules.pro",
                lambda s: s == "-keep class com.baidu.bottom.** { *; }",
                expect=1, label="drop baidu proguard keep")

op_delete_lines(APP, lambda s: s == "alias(libs.plugins.bilibilias.baidu.jar)",
                expect=1, label="drop baidu plugin alias")
op_delete_lines(APP, lambda s: s.startswith("val baiduStatId"),
                expect=1, label="drop baiduStatId")
op_delete_lines(APP, lambda s: s.startswith('manifestPlaceholders["BAIDU_STAT_ID"]'),
                expect=1, label="drop BAIDU_STAT_ID placeholder")
op_delete_lines(APP, lambda s: s.startswith('buildConfigField("String", "BAIDU_STAT_ID"'),
                expect=1, label="drop BAIDU_STAT_ID field")
op_delete_lines(APP, lambda s: s == "baiduStatDependencies()",
                expect=1, label="drop baidu deps call")
op_delete_function(APP, "fun DependencyHandlerScope.baiduStatDependencies(")
op_delete_block(APP, "if (!enabledPlayAppMode.toBoolean() && enabledAnalytics.toBoolean()) {",
                expect=1)
assert_absent(APP, "bilibilias.baidu.jar")
assert_absent(APP, "BAIDU_STAT_ID")

write_file("build-logic/convention/src/main/java/BaiduJarDownloadConventionPlugin.kt",
           "import org.gradle.api.Plugin\n"
           "import org.gradle.api.Project\n"
           "\n"
           "// 已停用：原实现在配置阶段从第三方地址下载百度统计 jar；本项目已移除百度统计。\n"
           "class BaiduJarDownloadConventionPlugin : Plugin<Project> {\n"
           "    override fun apply(target: Project) = Unit\n"
           "}\n")

# ------------------------------------------------------------------ 阶段 3
print("===== phase 3: UI cleanup =====")

# 3-A 安装包来源风险提示弹窗
op_sub_all(HS, "!packageSourceWarningDialogShow && ", "", expect=2)
op_replace_line(HS, "visible = !packageSourceWarningDialogShow,", ["visible = true,"])
op_delete_call(HS, "visible = packageSourceWarningDialogShow,", "dialog(")
op_delete_lines(HS, lambda s: s.startswith("var packageSourceWarningDialogShow"),
                expect=1, label="drop dialog state var")
op_delete_annotated_function(HS, "private fun PackageSourceWarningDialog(")
op_delete_lines(HS, lambda s: s == "val packageSourceWarningKey = remember { vm.getPackageSourceWarningKey() }",
                expect=1, label="drop key val")
op_delete_block(HS, "LaunchedEffect(packageSourceWarningKey, appSettings.packageSourceWarningSkipKey) {",
                expect=1)
assert_absent(HS, "packageSourceWarning")

# 3-B 首页三块的唯一数据通道关掉
op_stub_function(HVM, "fun initOldAppInfo()", "    fun initOldAppInfo() = Unit")

# 3-C 默认排版项收敛 + 老存档过滤回写
op_delete_enum_entries(ASR, "private fun createDefaultHomeLayoutItems()",
                       {"AppSettings.HomeLayoutType.Banner,",
                        "AppSettings.HomeLayoutType.Announcement,",
                        "AppSettings.HomeLayoutType.UpdateInfo,"})
op_replace_line(ASR,
                "val existingList = dataStore.data.first().homeLayoutTypesetList.toMutableList()",
                ["val storedLayout = dataStore.data.first().homeLayoutTypesetList",
                 "val existingList = storedLayout",
                 "    .filter {",
                 "        it.type == AppSettings.HomeLayoutType.Tools ||",
                 "            it.type == AppSettings.HomeLayoutType.DownloadList",
                 "    }",
                 "    .toMutableList()",
                 "if (existingList.size != storedLayout.size) {",
                 "    dataStore.updateData { it.copy(home_layout_typeset = existingList.toList()) }",
                 "}"])

# 3-D 设置 → 关于程序
op_delete_lines(SS, lambda s: s.startswith('CategorySettingsItem(text = "关于程序"'),
                expect=1, label="drop about category")
op_delete_call(SS, 'text = "关于",', "BaseSettingsItem(")
op_delete_call(SS, 'text = "版本追踪",', "BaseSettingsItem(")
op_delete_call(SS, 'text = "Github仓库",', "BaseSettingsItem(")
assert_absent(SS, "关于程序")
assert_absent(SS, "版本追踪")

print("")
print("===== SUMMARY: %d operation(s) applied =====" % len(DONE))
for d in DONE:
    print("  " + d)
