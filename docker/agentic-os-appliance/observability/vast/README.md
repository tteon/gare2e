# Vast 실험용 중앙 관측 스택

Vast의 GPU appliance가 보내는 OTLP trace와 지표를 중앙 Collector → Tempo·Prometheus → Grafana로 연결한다. 기존 실험에서 확인한 workload 대시보드 14개 패널을 포함한다. 루트 `compose.yaml`의 SDK 개발 스택과 기존 Linux host-network registry companion은 변경하지 않는다.

이 Compose는 **중앙 관측용**이다. GPU 이미지 설치, 모델 다운로드, 데이터셋 적재, pi 실행, Vast 대여·재시작을 수행하지 않는다. GPU appliance의 sampler·OTLP exporter가 별도로 동작해야 실제 실험 데이터가 보인다. 레지스트리 인증정보와 모델·원문·DB 파일은 이 스택에 넣지 않는다.

## 시작

저장소 루트에서 실행한다. `--project-directory .`를 유지해야 설정 파일의 상대 경로가 맞는다. macOS Docker Desktop과 Linux Docker에서 같은 bridge 네트워크를 사용하며, 호스트에 노출하는 포트는 모두 `127.0.0.1`에만 바인딩한다.

```bash
mkdir -p data/vast-observer
chmod 700 data/vast-observer
(umask 077; openssl rand -hex 32 > data/vast-observer/grafana_password)
export GARE2E_GRAFANA_PASSWORD_FILE="$(pwd)/data/vast-observer/grafana_password"

docker compose --project-directory . -p gare2e-vast-observer \
  -f docker/compose.vast-observability.yaml config --quiet
docker compose --project-directory . -p gare2e-vast-observer \
  -f docker/compose.vast-observability.yaml up -d
```

암호 파일은 최초 한 번만 생성하고 재실행 때 보존한다. Grafana 계정은 `admin`이며 익명 접근·회원가입은 꺼져 있다. 파일을 다시 생성해도 기존 Grafana volume의 계정 암호가 자동 변경되지는 않는다. 암호값을 Git이나 로그에 넣지 않는다. 실제 운영에서는 해당 파일을 읽을 수 있는 로컬 사용자도 제한한다.

Linux의 file-backed Compose secret은 호스트 파일 권한을 유지한다. 이 로컬 companion은 Grafana도 컨테이너 사용자 `0:0`으로 실행해, 호스트의 `0600` 파일을 읽도록 했다. 파일을 모든 사용자에게 읽히게 하거나 호스트 소유권을 바꾸지 않는다. privileged 모드나 Docker socket/호스트 루트 mount는 사용하지 않는다. 비root 실행이 필요한 운영 환경에서는 별도 secret 배치·권한 계약을 확인해야 한다.

| 연결 | 기본 주소 | 포트 재정의 환경변수 |
| --- | --- | --- |
| Grafana | `http://127.0.0.1:23000/d/gare2e-vast-workload` | `GARE2E_OBSERVER_GRAFANA_PORT` |
| Tempo HTTP | `http://127.0.0.1:23200` | `GARE2E_OBSERVER_TEMPO_PORT` |
| Prometheus | `http://127.0.0.1:29090` | `GARE2E_OBSERVER_PROMETHEUS_PORT` |
| Collector OTLP gRPC | `127.0.0.1:24317` | `GARE2E_OBSERVER_OTLP_GRPC_PORT` |
| Collector OTLP HTTP | `http://127.0.0.1:24318` | `GARE2E_OBSERVER_OTLP_HTTP_PORT` |

여러 스택을 켜면 다른 project 이름과 위의 다섯 포트를 모두 지정한다. container/network/volume 이름은 Compose project별로 분리되며 고정 `container_name`은 없다. 기본 SDK 스택과 같은 네트워크나 DB를 요구하지 않는다.

## GPU 호스트 연결

GPU 컨테이너 안에서 중앙 호스트의 loopback에 직접 접속할 수는 없다. 승인된 SSH 연결에서 reverse tunnel을 유지한다. 아래 변수에는 자신이 소유한 인스턴스의 접속값을 사용한다. 기존 owner의 터널이 있으면 재사용한다.

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -i "$VAST_SSH_KEY" -p "$VAST_SSH_PORT" \
  -R "127.0.0.1:14317:127.0.0.1:${GARE2E_OBSERVER_OTLP_GRPC_PORT:-24317}" \
  "$VAST_SSH_USER@$VAST_SSH_HOST"
```

GPU-side Collector의 중앙 OTLP gRPC exporter를 `127.0.0.1:14317`로 설정한다. 이 스택은 exporter를 자동 변경하지 않는다. 중앙 HTTP를 쓰는 exporter라면 별도 `14318 → 24318` tunnel과 HTTP/protobuf endpoint를 명시한다. vLLM 내부 metric endpoint를 인터넷에 열거나 Prometheus가 임의의 GPU 주소를 직접 scrape하게 하지 않는다.

GPU sampler가 관측한 `gare2e_gpu_*`, `gare2e_process_*`와 vLLM 지표는 OTLP로 보내고, 중앙 Prometheus는 Collector의 `8889` 및 자체 계측 `8888`을 scrape한다. 중앙 Collector에 hostmetrics receiver를 켜지 않았다. 중앙 컨테이너의 CPU/RAM을 GPU 호스트 자원으로 오인하지 않기 위해서다.

## 확인과 해석

```bash
curl --fail http://127.0.0.1:23200/ready
curl --fail http://127.0.0.1:29090/-/ready
curl --fail http://127.0.0.1:23000/api/health
curl --fail --get http://127.0.0.1:29090/api/v1/query \
  --data-urlencode 'query=up{job="seocho-otel-collector-self"}'
```

서비스 readiness만으로 실험 관측 완료를 판정하지 않는다. 실제 pi·모델·DB 한 사례를 보낸 뒤 정확한 trace/span ID로 Tempo `/api/traces/<trace_id>`를 조회하고, Prometheus에 해당 배포의 sampler 지표가 도착하는지 확인한다. Grafana 로그인 후 Prometheus·Tempo datasource와 14개 패널을 확인한다. 데이터가 없는 패널은 0으로 해석하지 않는다.

CPU는 process tree 누적 CPU 시간이고 GPU utilization은 sampler의 순간값이다. 요청별 GPU kernel 시간이나 병목의 직접 증거가 아니다. prefill/decode 패널도 엔진이 내보낸 server 지표이며 HTTP 시간을 임의로 분해하지 않는다. dashboard의 prefix 비율·rate는 표본과 시간 창이 필요하다. 프로토콜 검사용으로 넣은 지표는 실제 GPU 성능 결과와 구분한다.

## 보존과 종료

Tempo·Prometheus는 7일 보존 설정을 재사용한다. Grafana 설정과 중앙 trace 전송 큐는 별도 named volume에 보존한다. Collector의 persistent queue는 **Tempo로 보내는 trace**에 적용되며 OTLP metrics 전체나 GPU private raw의 백업을 뜻하지 않는다. 중앙 호스트가 꺼지면 실시간 조회·수신은 중단된다. GPU-side spool/queue 보존도 별도로 확인해야 한다.

```bash
docker compose --project-directory . -p gare2e-vast-observer \
  -f docker/compose.vast-observability.yaml down
```

`down`은 해당 project의 컨테이너·네트워크만 정리하고 named volume을 보존한다. 검증 전 `down -v`로 자료를 지우지 않는다. 이 명령은 Vast 인스턴스를 stop/destroy하지 않는다. S3 업로드·GET SHA, Notion 기록, GRBench 적재·평가, pi/GraphCoT 연결과 성능 비교는 별도 gate이며 이 Compose 기동만으로 완료되지 않는다.
