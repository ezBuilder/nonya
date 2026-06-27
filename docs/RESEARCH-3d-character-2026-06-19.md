# 연구: 게임급 3D 캐릭터 + 커스텀 시스템 (2026-06-19)

> 목표: nonya 펫(WKWebView/three.js 투명 오버레이)에 **실제 리깅 모델**을 로드해 게임급 캐릭터로, 사용자가 좋아하는 캐릭터를 쓰는 **커스텀 시스템**까지. 손제작 프리미티브 폐기.
> 출처: deep-research run `wt0ygul8t` (106 에이전트, 2.85M 토큰). 검증: 대부분 3-0(three.js/@pixiv/three-vrm 공식 문서·VRM 사양·모델 라이선스 페이지).

---

## 1. 결론 — 포맷·로더

- **VRM(@pixiv/three-vrm)을 캐릭터 펫 1순위**, **glTF/GLB(GLTFLoader)를 범용 베이스**로. **[검증]**
  - three-vrm는 **MIT 라이선스** → 상용 .app 번들 가능.
  - 로드: `loader.register(p => new VRMLoaderPlugin(p))` → `gltf.userData.vrm`. (VRM이 아니면 `gltf.scene` = 일반 glTF.)
  - GLTFLoader는 core가 아닌 **addon**(`three/addons/loaders/GLTFLoader.js`).
- **버전 주의**: three-vrm v3는 three **r150+** 필요. 현재 데모는 r128+GLTFLoader(glTF만). 통합 시 three+three-vrm 호환 버전으로 번들. **[설계]**

## 2. 상태 → 표정/애니 구동 (핵심)

- **VRM 1.0 표준 표정 프리셋 5종**: `happy/angry/sad/relaxed/surprised`(+neutral). `vrm.expressionManager.setValue('angry',1.0)`로 코드 구동. **[검증]**
  - nonya 매핑: **scolding→angry · stuck→sad 또는 surprised · working→relaxed/neutral · watching→neutral**.
  - 단 표정 시각화는 모델 작자 재량(프리셋 존재는 표준, 룩은 모델마다 다름).
- **휴머노이드 정규화 본**: `getNormalizedBoneNode(name)`로 절차적 포즈·lookAt·아바타 간 리타겟(T-pose 기준). **[검증]**
- **애니메이션**: three.js `AnimationMixer` 하나로 클립(bones)·블렌드셰이프·머티리얼을 통합 구동, **idle 베이스 + 리액션 오버레이 블렌드** 가능. **[검증]**
- **Mixamo 리타겟**: three-vrm 공식 예제(`loadMixamoAnimation.js`+`mixamoVRMRigMap.js`)가 Mixamo 클립을 VRM 리그에 적용. → idle/angry gesture 등 풍부한 애니 확보. **[검증]**

## 3. 번들 가능(재배포) 게임급 모델 소스 — 라이선스 주의

| 소스 | 라이선스 | 번들 배포 | 비고 |
|---|---|---|---|
| **Quaternius** | CC0 | ✅ 수정·합성·상용·무귀속 | 캐릭터/소품 팩 |
| **VRoid Studio (Stable Ver.) VRM** | 상용·재판매 허용 | ✅ | 아바타 생성, '사용자 캐릭터' 핵심 |
| **Mixamo 애니메이션** | 로열티프리(상용) | ⚠️ **앱에 구워넣기만** | 원본 .fbx/.glb를 추출가능 형태로 동봉 **금지** |
| **three.js RobotExpressive** | CC0 | ✅ | 14종 애니(Idle/Punch/No/ThumbsUp…) — 내장 기본캐릭터로 적합 |
| **Ready Player Me** | 상용 약관(아바타 GLB) | △ | ARKit 블렌드셰이프(`?morphTargets`) — **단 Netflix 인수 후 서비스 불확실** |
| Sketchfab **CC BY-NC** | 비상업 | ❌ | 번들 금지(제외) |

> ⚠️ **공통 법적 주의**: 모델에 박힌 제3자 에셋은 각자 라이선스 유지. 번들 전 모델별 라이선스 확인 필수.

## 4. 커스텀 캐릭터 임포트

- 사용자가 **`.glb`/`.vrm` 파일을 선택/드롭** → GLTFLoader(+VRMLoaderPlugin)로 로드. `userData.vrm` 있으면 VRM, 없으면 glTF. **[검증]**
- VRoid로 만든 VRM, Mixamo 캐릭터, Ready Player Me 아바타 임포트 가능.
- 검증(파싱 성공)·썸네일(첫 프레임 캡처)·폴백(실패 시 기본 캐릭터).

## 5. 게임급 룩 렌더 — **[설계, 연구 미검증]**
연구가 1차 출처로 검증하진 않았으나 표준 기법:
- PBR 머티리얼 + **환경광**(RoomEnvironment 또는 HDRI) + `outputEncoding=sRGB`.
- **ACES 톤매핑**(`ACESFilmicToneMapping`) + 노출 튜닝.
- 안티에일리어싱(MSAA), **키+림+헤미** 조명, `ShadowMaterial` 그림자(투명 오버레이에 자연스러운 접지).
- 투명 배경: `alpha:true`, 그림자는 ShadowMaterial로만(바닥 메시 투명).
- (이 머신 데모로 Fox·RobotExpressive 렌더해 기법 유효성 1차 확인.)

## 6. 성능 — **[설계, 연구 미검증]**
- 항상-위 투명 WebGL 상시 구동 → **idle 시 프레임 제한**(예: 평소 12~15fps, 리액션 시 60fps), 폴리/텍스처 예산 관리.
- WKWebView three.js는 동작하나 배터리 영향 측정 필요.
- 모델 파일 크기·코드사이닝/공증 상호작용은 미검증 → 통합 시 실측.

## 7. nonya 구현 계획
1. 펫 렌더러를 three(r150+)+GLTFLoader+three-vrm으로 교체, 로컬 번들.
2. 내장 기본캐릭터: **RobotExpressive(CC0)** + 가능시 Quaternius/VRoid VRM 1종.
3. `loadModel(path)` → glTF/VRM 자동 판별, AnimationMixer + (VRM)expressionManager.
4. 상태→(VRM)표정 프리셋 + 애니클립 매핑(scolding=angry+Punch, stuck=sad+No, working=relaxed+ThumbsUp, watching=neutral+Idle).
5. 커스텀 임포트: Swift 파일선택 → 모델 폴더 복사 → 펫 재로드. 스킨 메뉴에 사용자 캐릭터 노출.
6. 게임급 룩(§5) + idle 프레임 제한(§6). 번들·공증 재검증.

---

*검증 통계: 다수 각도 3-0. 미검증(설계 판단): 게임급 렌더 셋업·성능/배터리·번들/공증 상호작용. RPM 서비스 지속성·VRoid 문구는 WebSearch 확인(직접 페이지 403/오류).*
