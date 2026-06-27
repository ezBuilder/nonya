# nonya — 캐릭터 크레딧 / 라이선스

펫 캐릭터 로스터의 출처와 재배포 조건. **상용 배포 시 반드시 준수.**

## 기본(번들) 캐릭터 — 재배포 가능

| 캐릭터 | 출처 | 작가 | 라이선스 | 의무 |
|---|---|---|---|---|
| 死神 (Reaper) | Poly Pizza | Falibu | **CC-BY 3.0** | 앱 내 크레딧/귀속 표기 필수 |

死神은 nonya DMG에 **번들되는 유일한 기본 캐릭터**다. CC-BY라 상용 배포 가능하되,
앱 정보/About 화면에 `"Reaper" by Falibu (Poly Pizza), CC-BY 3.0` 귀속을 표기해야 한다.

## Mixamo 캐릭터 — 사용자 본인 계정 임포트 (번들 배포 금지)

Remy · Warrok · Mutant · Vanguard · Exo Gray · Vampire · Skeleton Zombie · Ely ·
Erika Archer · The Boss — 전부 **Adobe Mixamo** 출처.

- Mixamo 라이선스: 본인 프로젝트(상용 포함)에 **로열티 프리**로 사용 가능하나,
  **원본 캐릭터 파일(FBX/glb/usdz)의 재배포는 금지**.
- 따라서 nonya 배포본에는 절대 번들하지 않는다. 각 사용자가 **자기 Adobe/Mixamo
  계정**으로 받아 `models/roster/`(또는 `models/incoming/`)에 넣으면 로스터에 자동 등장.
- 이 저장소의 `models/`는 `.gitignore` 처리되어 커밋/배포에 포함되지 않음(확인필).

## 커스텀 캐릭터 (사용자 임포트)

`models/roster/` 또는 `models/incoming/`에 `usdz/scn/obj`(또는 변환 경유 `glb/fbx`)를
넣으면 메뉴바 "캐릭터"에 자동 추가. 사용자가 넣는 파일의 라이선스는 사용자 책임.

## 파이프라인 (재현용)

FBX/glb → `build/shots/convert_one.sh <Name> <fbx> <port>` →
three.js USDZExporter(헤드리스) → localhost 싱크 → `models/roster/<Name>.usdz`(텍스처 보존).
펫은 `SCNScene(url:)` 네이티브 로더로 로드(MDLAsset은 usdz 텍스처 누락하므로 금지).
