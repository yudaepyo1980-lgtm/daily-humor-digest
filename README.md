# humor_bot

루리웹 유머 베스트 글을 읽어서, 같은 게시판 말투로 가볍게 다시 쓴 뒤
대상 게시판에 올린다. GitHub Actions 에서 돌아서 컴퓨터가 꺼져 있어도 된다.

## 동작

1. 루리웹 유머 베스트 1~3페이지를 읽고, 추천수 높은 순으로 대기열을 만든다.
   (한 시간에 한 번 새로 읽는다.)
2. 마지막 실행 이후 흐른 시간 x (시간당 2~3편) 만큼 올린다. GitHub 예약 실행이 몇 시간씩
   밀려도 밀린 만큼 따라잡고(최대 4시간치, 하루 72편 상한), 편 사이는 들쑥날쑥하게 띄운다
   (지수분포 랜덤 간격: 45초~45분, 평균 약 20분). 한 번 실행은 최대 150분까지 이어지고,
   남은 편수는 다음 실행으로 넘긴다.
3. 글 하나마다: 본문/이미지 읽기 -> Claude 가 재작성 -> 이미지를 어드민에 업로드 -> 글 등록.
4. 이미 본 글은 비공개 저장소의 `state.json` 의 `seen` 에 남겨서 다시 올리지 않는다.
   같은 제목이 다른 번호로 올라와도 걸러낸다.
5. 닉네임은 비공개 저장소의 `nicknames.json` 50개에서 랜덤, 최근 10개는 피한다.

건너뛰는 글: 영상/임베드가 있는 글, 이미지가 10장을 넘는 글, 내용이 거의 없는 글,
재작성 단계에서 제외로 판단된 글(선정성·정치·사건사고 등).

## 로컬 실행

```bash
export CLAUDE_CODE_OAUTH_TOKEN=...      # claude setup-token 으로 발급
export SITE_LOGIN_ID=...
export SITE_PASSWORD=...
export STATE_DIR=/path/to/private       # site_config.json, nicknames.json, state.json 이 있는 폴더

python -m bot.run --dry-run             # 등록 없이 out/ 에 미리보기
python -m bot.run --count 1             # 지금 1편 등록
python -m bot.run                       # 스케줄대로
```

## 구성: 공개 저장소 + 비공개 저장소

- 이 저장소(공개)에는 범용 코드만 있다. 사이트 주소/API 모양/닉네임/올린 글 기록은 없다.
- 비공개 저장소에 `site_config.json`, `nicknames.json`, `state.json` 을 둔다.
  워크플로가 실행할 때 내려받고, 끝나면 `state.json` 만 다시 올린다.

## GitHub 설정

공개 저장소 Settings > Secrets and variables > Actions 에 다섯 개를 넣는다.

| 이름 | 값 |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | `claude setup-token` 으로 만든 토큰 |
| `SITE_LOGIN_ID` | 로그인 아이디 |
| `SITE_PASSWORD` | 로그인 비밀번호 |
| `PRIVATE_REPO` | 비공개 저장소 이름 (`계정/저장소`) |
| `PRIVATE_REPO_TOKEN` | 비공개 저장소 하나에만 읽기/쓰기 권한을 준 토큰 (fine-grained, Contents: Read and write) |

Actions 탭 > Humor Bot > Run workflow 로 수동 실행할 수 있다. `count` 에 숫자를 넣으면
스케줄과 상관없이 그만큼 바로 올린다. 처음에는 `1` 로 한 번 돌려 보는 걸 권한다.

## 멈추는 법

Actions 탭에서 워크플로를 Disable 하거나, `humor.yml` 의 `schedule` 블록을 지운다.

## 주의

- 스케줄은 GitHub 사정으로 몇 시간씩 밀릴 수 있다. 그래서 따라잡기 방식이다.
- 소스를 바꾸려면 `bot/source_<이름>.py` 를 만들고 `SOURCE` 환경변수를 바꾼다.
- 이 저장소가 공개이므로 Actions 시간은 무료다. 대신 실행 로그가 공개되어, 로그에는 글
  제목/닉네임을 남기지 않는다 (Actions 에서는 건수만 출력).
- 공개 저장소는 60일간 활동이 없으면 예약 실행이 꺼진다. `keepalive.yml` 이 매달 빈 커밋을 남겨 막는다.
- 소스 사이트 이용약관과 이미지 권리는 운영자가 확인한다.
