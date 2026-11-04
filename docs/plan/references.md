# 참고 구조와 차용 범위

[문서 안내](README.md)

## ReportAnchor JS 문서 구조

요청한 [ReportAnchor의 JS 브랜치 docs](https://github.com/Jungminii-1114/ReportAnchor/tree/JS/docs)를 확인하여 문서 계층과 책임 분리 방식을 참고했다. 검토한 트리의 커밋은 `3967b0ffad2f1e01d819bcaefb8fd5223de53240`다. 아래 고정 링크는 참고 시점의 내용을 가리킨다.

- [전체 문서 파이프라인](https://github.com/Jungminii-1114/ReportAnchor/blob/3967b0ffad2f1e01d819bcaefb8fd5223de53240/docs/document-pipeline.md)
- [명칭과 계층](https://github.com/Jungminii-1114/ReportAnchor/blob/3967b0ffad2f1e01d819bcaefb8fd5223de53240/docs/naming-conventions.md)
- [Big Node 목록](https://github.com/Jungminii-1114/ReportAnchor/blob/3967b0ffad2f1e01d819bcaefb8fd5223de53240/docs/big-nodes/README.md)
- [T-01 업무 흐름](https://github.com/Jungminii-1114/ReportAnchor/blob/3967b0ffad2f1e01d819bcaefb8fd5223de53240/docs/big-nodes/T-01.md)
- [Small Node 목록](https://github.com/Jungminii-1114/ReportAnchor/blob/3967b0ffad2f1e01d819bcaefb8fd5223de53240/docs/small-nodes/README.md)
- [N-01 책임·내부 함수·입출력](https://github.com/Jungminii-1114/ReportAnchor/blob/3967b0ffad2f1e01d819bcaefb8fd5223de53240/docs/small-nodes/N-01.md)
- [N-04 생성 계약](https://github.com/Jungminii-1114/ReportAnchor/blob/3967b0ffad2f1e01d819bcaefb8fd5223de53240/docs/small-nodes/N-04.md)
- [공통 결과 계약](https://github.com/Jungminii-1114/ReportAnchor/blob/3967b0ffad2f1e01d819bcaefb8fd5223de53240/docs/small-nodes/common-result-contract.md)

차용한 것은 Overall → Big Node → Small Node의 읽기 구조, 업무 흐름과 단일 책임의 분리, 내부 함수를 별도 노드로 과분할하지 않는 원칙, 실행 상태와 업무 결과를 구분하는 공통 반환 방식이다.

ReportAnchor의 업무 단계·코드·개발 상태를 프토벤의 상태로 옮기지 않는다. 프토벤의 B/S/T ID, CV 입력, 도구 staging, 명령 적용·ACK, 관측 불확실성, 가상 입력·접촉 구분은 프토벤 목적에 맞춰 별도로 설계한다.

## 기능 확인용 공식 자료

- [MediaPipe Hand Landmarker](https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker)
- [Ollama Tool Calling](https://docs.ollama.com/capabilities/tool-calling)
- [Ollama Structured Outputs](https://docs.ollama.com/capabilities/structured-outputs)
- [LM Studio 개발자 문서](https://lmstudio.ai/docs/developer)
- [MLX LM](https://github.com/ml-explore/mlx-lm)
- [llama.cpp](https://github.com/ggml-org/llama.cpp)
- [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)

공식 자료는 기능과 실행 구조를 이해하기 위한 참고다. 프토벤의 실카메라 성능·Agent 품질·사용자 학습 효과를 입증하는 자료는 별도 평가로 확보한다.
