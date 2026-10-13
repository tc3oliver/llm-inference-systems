# 研究後續（postscript）

<!--
已於 2026-09-22 發佈：接在 study.meowcoder.com 的〈償還 reusable state 的債〉
(https://study.meowcoder.com/posts/260921-canonical-state-debt-recovery/) 結論
之後、出處段之前，標題降一級以配合該文的層級。同一次發佈把文末那句「#3793
（draft，寫這篇時還沒送審）」改成它現在的狀態；那是唯一動到的正文。

2026-10-03 更新並發佈：#3793 關閉、改由 #3964 送出；#3840、#3842 已合併；文末
那句同步改成 #3964，#3792 註明上游審查後只剩註解修正。

2026-10-13 改寫（發佈時一併套用到文章）：〈機制送上游了，還沒有被合併〉改寫成
維護者於 2026-10-12 關閉 #3964、沒有合併；#3811 不再寫成相依於 #3964；〈EXP-003
現在的狀態〉同步改寫。「補回」沿用正文的用法。除了本檔，正文還動到兩處，其餘正文
不改：
- 〈與 runtime 既有工作的關係〉：#3330 已於 2026-10-07 關閉、沒有合併，那段改用
  過去式，並刪掉「兩者是否該共用同一套背景工作機制」那一句。開頭第一句改成：
  「[omlx#3330](https://github.com/jundot/omlx/pull/3330)
  提議加入 Metal keepwarm 與精確的 prompt-tail 物化，2026-10-07 沒有合併就關了。」
  「兩者目前在範圍上也不重疊」改成「兩者在範圍上也不重疊」。
- 文末出處段（原文是「……；開放中，尚未合併）」）整段改成：

  上游的實作：功能是 oMLX PR
  [#3964](https://github.com/jundot/omlx/pull/3964)（接替 2026-09-26 關閉的
  [#3793](https://github.com/jundot/omlx/pull/3793)；維護者在 2026-10-12 關掉，
  沒有合併）。做這個工作時
  另外送過一個與本功能無關的修正
  [#3792](https://github.com/jundot/omlx/pull/3792)，上游審查後確認那條路徑走不到，
  現在只改一段註解。整個研究計畫的短版在
  [LLM Inference Systems](https://meowcoder.com/zh/work/llm-inference-systems/)。

這份檔案是這段文字在本 repo 的正本：文章與這裡不一致時，以這裡為準。正文其餘
部分不改——正文寫的是實驗，這一段寫的是實驗之後、把機制送上游時發生的事。

同日追加並發佈〈把它放進真實 workload，又掉出兩個獨立的缺陷〉一節，接在
〈把它做到 production 品質〉之後、〈EXP-003 現在的狀態〉之前。

原則與這個 repo 一致：
- 不寫成 PR changelog，不列 test 數字，不貼完整記憶體表格。
- 只有已經合併的 PR 才寫成已合併。
- 不宣稱前景延遲或 admission headroom 有改善。
- 數字一律回連 repo。
-->

文章寫完之後，我把這個機制整理成上游的 pull request，過程中又冒出幾件文章裡沒寫的
事。

## 機制送上游，維護者把它關了

背景 canonical state recovery 最早以
[omlx#3793](https://github.com/jundot/omlx/pull/3793) 送出；那個 pull request 後來
多包進一個後續功能，2026-09-26 關掉了，同一份補回程式碼改以
[omlx#3964](https://github.com/jundot/omlx/pull/3964) 重新送出。維護者在 2026-10-12
沒有合併就把它關了。

維護者給了兩個理由。第一，補回是在閒置時把跳過的 token 當一般的 dense prefill 重
算一遍，所以 SpecPrefill 加補回做的事比直接關掉還多，大部分輪次頂多追平關掉；他還
指出我的 benchmark 沒有關掉 SpecPrefill 的對照。第二，這個 PR 加了約 7,500 行，其中
約 1,400 行是 runtime，主要在 `scheduler.py`，那個檔案幾乎天天在改，他沒辦法為了
一個要開 SpecPrefill 才有用的實驗性功能長期維護這麼多 code。

第一點只對一半。threshold 8,192 那次實驗有跑一個關掉 SpecPrefill 的 session
（285.78 秒），但同一次實驗的補回從第 1 輪起走的路徑跟關掉一樣，都是 dense：第 1
到 6 輪合計，補回 190.97 秒，關掉 182.05 秒，補回慢約 9 秒。補回那次整個 session
比關掉快 71 秒，這 71 秒是 SpecPrefill 在第 0 輪省的（約 80 秒，23.81 秒對 103.72
秒），後面幾輪又慢掉約 9 秒。補回一直走 sparse 的設定（threshold 1，228.38 秒降
到 79.06 秒）和關掉的那次不能乾淨地比：那次 prompt 最大的第 5、6 輪撞上 memory
throttle 而暫停，sparse 的 session 沒碰到。所以 228.38 秒降到 79.06 秒只能說補回比
只開 SpecPrefill 好。

維護者引的 TTFT 中位數（SpecPrefill 關 3.1 秒、開 43.8 秒）是 #3964 討論串裡另一位
使用者在別台機器、別的模型上量的，沒開補回。維護者把 PR 關了，我沒意見，會在自己的 branch 繼續做；補回要是能比關掉
SpecPrefill 快，再想更小的實作。

## 驗證 PCSR 時掉出一個獨立的正確性缺陷

為了確認補回的 canonical state 真的等同於 dense 讀過一遍的狀態，我必須把 sparse
與 dense 兩條路徑放在一起比。這個比較暴露出一件跟背景補回無關的事：在 mRoPE 的
VLM 上，SpecPrefill 把選中的 token 寫在**壓縮之後**的位置，而不是它們在原始序列裡
的位置。

這是 [omlx#3811](https://github.com/jundot/omlx/pull/3811)。

兩件事要講清楚。**PCSR 沒有造成這個缺陷**——把背景補回整個關掉，它一樣存在；PCSR
提供的只是那個讓它現形的 dense 對照。而修好它之後改善了什麼，也不能多講：位置契約
恢復了，首個 token 的 logit 在六輪裡六輪都更靠近 dense baseline，但在這個 workload
上 greedy 輸出的一致性並沒有因此變好。那是量出來的負面結果，不是還沒量。

## 把它做到 production 品質，逼出三個系統教訓

真正讓我意外的是這一段。把一個在研究 build 上跑得好好的機制整理到可以被審查，又找
出六個缺陷——而實驗自己的 workload 一個都碰不到。它們需要的條件是：同一個 process
裡載了第二個模型、MTP 打開、補回途中遇到 eviction、prompt 長度剛好是 cache block
的整數倍。這些條件在 `data/` 的任何一輪裡都不存在。

所以 `data/` 裡沒有任何一個數字因此改變。這是在講那些跑的覆蓋範圍，不是在替它辯護。

六個裡有三個不只是這個 runtime 的問題。

**背景工作要讓出的是所有權，不只是執行。** 補回的任務停止計算之後，仍然抱著它那份
materialized 的 dense cache——那是實際配置出來的陣列，不是對 paged pool 的參照。排
程器裡每一個推理「誰在用裝置」的原語，都會把這種任務報成閒置。最尖的一個情況是：
記憶體壓力觸發的 throttle 會去回收 buffer pool，而它回收的時候，任務手上還握著自己
擁有的最大一塊配置。

契約是這樣寫的：**已發佈的 canonical state 是耐久的；正在進行、還沒發佈的重建狀態
是可丟棄的。**

這裡要很小心地說清楚量到了什麼。滯留狀態的大小是量出來的，而且對 context 完全線
性；把它放掉之後 MLX 的 active memory 會如數回來，也是量出來的。**前景延遲或
admission headroom 有沒有因此改善，沒有建立。** process 的實體 footprint 在
allocator 收斂之後根本不動，而且同樣的工作跑兩次，基線本身的變動就比想找的效果大。
這個修正是靠所有權的契約成立的，不是靠一個量到的前景改善。

**前景的優先權需要在執行之前就可見。** 排程器手上的儀器——decode registry、prefill
tracker——回答的都是「有沒有東西正在跑」。背景任務需要的是「有沒有東西正在等」，而
這兩個問題之間的那段區間，正好就是讓路還有意義的區間。到達的可見性必須是跨 process
的，必須在執行之前存在，而且必須從到達一直維持到請求離開，不是到 admission 為止。

**Cache 的 watermark 不是 cache 的事實。** `committed_tokens` 是記帳，只會往上走；
它所宣稱的那些 block 卻可以被 evict 掉。兩邊各自移動，而只有在它們被比較的那一瞬間
，前者才是後者的證據。任何一個要把 watermark 帶過時間的元件，都必須能讓它往回走。

這三條的完整版本、各自的不變式與回歸測試，在 repo 的
[`HARDENING.md`](https://github.com/tc3oliver/llm-inference-systems/blob/main/experiments/exp-003-progressive-shadow-prefill/HARDENING.md)；
記憶體那一組的六個量測點、收斂控制組，以及哪些是 derived、哪些是 not established，
在
[`data/recovery-foreground-qos/`](https://github.com/tc3oliver/llm-inference-systems/tree/main/data/recovery-foreground-qos)。

## 把它放進真實 workload，又掉出兩個獨立的缺陷

後來這個機制被放進真實的 Claude Code workload 做 production validation。它在那裡
沒有照受控實驗預測的方式啟動，於是我把 target 與 draft 兩個 cache 角色分開量，結
果發現 SpecPrefill 的 draft cache 從來沒有被重用過一次：draft 這條路徑沒有在 cache
邊界保存 recurrent 狀態，所以每個存下來的 block 都只帶一個 placeholder，walk-back
每次都正確地找不到東西可以還原
（[omlx#3842](https://github.com/jundot/omlx/pull/3842)）。把那條重用路徑打通之
後，後面還藏著第二個：還原成功的 draft cache 會被當成空的，因為 scoring 從
recurrent 的第 0 層去讀它的邏輯位置，而那一層根本沒有位置
（[omlx#3840](https://github.com/jundot/omlx/pull/3840)）。這兩個修正都已在
2026-09-24 合併進 oMLX 上游。

兩件事都不在 PCSR 裡，也**不是**前面那六個 hardening 缺陷的第七、第八個。它們在第
二條 cache 路徑上，是把機制帶進真實 workload 才會踩到的問題，EXP-003 的結論與收尾
條件都不因此改變。細節、資料與限制在 repo 的
[hybrid draft prefix reuse](https://github.com/tc3oliver/llm-inference-systems/blob/main/research-threads/specprefill-draft-cache-reuse.md)。

## EXP-003 現在的狀態

研究結果完成；上游提案已關閉。問題回答了，機制建立了，受控與真實 workload 都跑
了，限制也寫下來了。還缺 threshold 1 的補回和關掉 SpecPrefill 之間的乾淨對照。
#3811 是獨立的修正，還在等上游審查，我這邊決定不了。
