# 모델 인테이크 (여기에 캐릭터 파일을 넣어줘)

AI 3D 생성 서비스에서 뽑은 캐릭터를 **이 폴더에 넣으면** nonya가 받아서
변환(USDZ) → 리깅/애니 매핑 → 펫 통합 → 미리보기 렌더까지 처리한다.

## 1. 어디서 뽑나 (무료 크레딧 OK)
- **Meshy** (meshy.ai) — 캐릭터 강함, text/image→3D, 리깅·애니 지원, GLB 익스포트
- **Tripo** (tripo3d.ai) — 무료 티어 넉넉, 빠름, GLB
- **Rodin / Hyper3D** (hyper3d.ai) — 고디테일

## 2. 가장 실사에 가깝게 뽑는 법
- **image-to-3D** 우선: 원하는 캐릭터의 실사/컨셉 이미지 1장 업로드 → 그게 text보다 훨씬 실사.
- 옵션: **PBR/Textured ON**, **Quality High**, 가능하면 **Rigged(리깅) ON**, **A-pose 또는 T-pose**.
- 익스포트: **GLB**(텍스처 포함) 권장. USDZ/FBX도 받음.

## 3. 프롬프트 예시 (death-god 컨셉; 원하는 캐릭터로 바꿔도 됨)
> A hooded grim reaper / shinigami character, tattered dark robe, glowing
> red eyes, skeletal hands, holding a scythe and a black notebook,
> highly detailed, realistic PBR materials, full body, A-pose, game-ready.

(다른 캐릭터 원하면 그 묘사로 — 커스텀 임포트라 뭐든 됨.)

## 4. 넣는 법
- 이 폴더(`models/incoming/`)에 `.glb` / `.usdz` / `.fbx` 파일을 복사.
- "모델 넣었어"라고 말하면 nonya가 잡아서 통합 시작.

## 받는 포맷
glb · gltf · usdz · usd · fbx (fbx는 변환 경유). 텍스처가 별도 파일이면 같이 넣어줘.
