# Hướng dẫn sử dụng Copilot Operator

## Nó là gì?

**Copilot Operator** là một cái vòng lặp tự động ngồi *điều khiển* GitHub Copilot Chat thay cho bạn.

Thay vì bạn phải:
1. Gõ prompt vào Copilot
2. Đợi kết quả
3. Xem có đúng không
4. Gõ tiếp prompt "fix cái này đi"
5. Lặp lại 10 lần...

→ **Operator làm hết việc đó.** Bạn chỉ cần nói mục tiêu, rồi đi làm việc khác.

---

## Nó tự chạy được không — không cần ngồi canh?

**Có.** Đó là mục tiêu chính.

Khi bạn chạy lệnh, operator sẽ:

```
Bạn nói mục tiêu
        ↓
Operator tự phân tích codebase
        ↓
Gửi prompt cho Copilot Chat
        ↓
Đọc kết quả trả về
        ↓
Chấm điểm (0–100), kiểm tra test/lint
        ↓
Nếu chưa xong → gửi thêm prompt, tiếp tục
Nếu xong rồi → dừng, báo kết quả
```

Toàn bộ vòng lặp đó **tự chạy** mà không cần bạn làm gì. Mặc định tối đa 6 vòng, có thể tăng lên.

---

## Cài đặt

### Yêu cầu

- Python 3.10+
- VS Code đang mở workspace
- GitHub Copilot extension được cài trong VS Code
- Lệnh `code` có trong PATH (mở terminal gõ `code --version` phải ra version)

### Cài

```bash
pip install copilot-operator
```

Hoặc từ source (đang ở folder này):

```bash
pip install -e .
```

### Kiểm tra cài xong chưa

```bash
copilot-operator version
# → copilot-operator 1.0.0

copilot-operator doctor
# → kiểm tra tất cả thứ cần thiết, báo [ok] hoặc [warn]
```

---

## Lần đầu dùng — 3 bước

### Bước 1: Khởi tạo workspace

Vào thư mục project của bạn, chạy:

```bash
cd /path/to/your-project
copilot-operator init
```

Lệnh này tạo ra:
- `copilot-operator.yml` — file cấu hình chính
- `.copilot-operator/` — thư mục lưu trạng thái, memory, logs
- `docs/operator/` — "bộ nhớ" về project (architecture, traps, definition of done)

### Bước 2: Chỉnh cấu hình (tuỳ chọn)

Mở `copilot-operator.yml`:

```yaml
workspace: .
maxIterations: 6        # số vòng lặp tối đa
targetScore: 85         # điểm tối thiểu để coi là "xong"
validation:
  - name: tests
    command: "npm test"   # hoặc pytest, cargo test, v.v.
    required: true
  - name: lint
    command: "npm run lint"
    required: false
```

### Bước 3: Chạy

```bash
copilot-operator run --goal "Fix the login bug where tokens expire too early"
```

Rồi đi uống cà phê. Khi xong nó tự báo.

---

## Các lệnh hay dùng

### Chạy với mục tiêu

```bash
# Chạy bình thường
copilot-operator run --goal "Add unit tests for the auth module"

# Chạy và thấy tiến trình ngay trên terminal (có màu)
copilot-operator run --goal "Refactor the database layer" --live

# Thử prompt xem có đúng không — không thực sự gửi cho Copilot
copilot-operator run --goal "..." --dry-run

# Giới hạn số vòng lặp
copilot-operator run --goal "..." --max-iterations 3
```

### Các loại goal profile

```bash
# Sửa bug
copilot-operator run --goal "Fix NullPointerException in UserService" --goal-profile bug

# Thêm feature
copilot-operator run --goal "Add pagination to the product listing API" --goal-profile feature

# Refactor
copilot-operator run --goal "Extract the validation logic into a separate module" --goal-profile refactor

# Viết docs
copilot-operator run --goal "Write docstrings for all public functions in api.py" --goal-profile docs

# Review / audit
copilot-operator run --goal "Review the auth module for security issues" --goal-profile audit
```

### Xem trạng thái

```bash
# Xem run đang ở đâu
copilot-operator status

# Xem milestone plan hiện tại
copilot-operator plan

# Xem đang làm task gì
copilot-operator focus

# Xem liên tục (tự cập nhật mỗi 2 giây)
copilot-operator watch
```

### Tiếp tục run bị gián đoạn

```bash
# Run bị Ctrl+C, hết pin, mạng đứt... → tiếp tục từ chỗ dừng
copilot-operator resume

# Tiếp tục nhưng đổi mục tiêu
copilot-operator resume --goal "Actually, focus on the login flow first"
```

### Fix GitHub issue tự động

```bash
# Đặt token trước
export GITHUB_TOKEN=ghp_...

# Operator tự đọc issue #42, hiểu nội dung, rồi fix
copilot-operator fix-issue --issue 42 --repo owner/repo-name
```

### Dọn dẹp logs cũ

```bash
copilot-operator cleanup --keep-runs 10
```

---

## Kết nối LLM (tuỳ chọn — không bắt buộc)

Mặc định operator chạy ở chế độ **heuristic** (logic thuần, không cần API key).

Nếu bạn có API key LLM, operator sẽ thông minh hơn — phân tích code tốt hơn, gợi ý strategy hay hơn.

```bash
# OpenAI
export COPILOT_OPERATOR_LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-...

# Anthropic Claude
export COPILOT_OPERATOR_LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...

# Gemini
export COPILOT_OPERATOR_LLM_PROVIDER=gemini
export GEMINI_API_KEY=...

# Ollama chạy local (miễn phí, không cần internet)
export COPILOT_OPERATOR_LLM_PROVIDER=local
# không cần API key
```

Kiểm tra sau khi cài:

```bash
copilot-operator brain
copilot-operator brain --test "Explain what this project does"
```

---

## Operator tự làm những gì?

Khi bạn gõ `copilot-operator run --goal "..."`, đây là những gì xảy ra **tự động**:

### 1. Phân tích codebase
- Quét toàn bộ file source (Python, JS/TS, Go, Rust, Java, C#, v.v.)
- Lập bản đồ class/function (như Aider)
- Inject vào prompt để Copilot hiểu cấu trúc code

### 2. Lên kế hoạch (milestone plan)
- Tự phân loại goal là bug/feature/refactor/docs
- Tạo plan 2–5 milestone với tasks cụ thể
- Theo dõi progress qua từng vòng

### 3. Gửi prompt cho Copilot
- Prompt có đầy đủ: ngữ cảnh, bản đồ code, standards, guardrails
- Gửi qua VS Code CLI (`code chat --mode agent`)

### 4. Đọc và đánh giá kết quả
- Parse response, lấy điểm (0–100)
- Chạy `npm test` / `pytest` / lint tự động
- Phát hiện loop (Copilot lặp đi lặp lại không tiến)
- Phát hiện score regression

### 5. Quyết định tiếp theo
- **Tiếp tục** nếu chưa xong
- **Replan** nếu hướng đi sai
- **Rollback** (git stash pop) nếu score tụt
- **Dừng** nếu đã xong hoặc bị kẹt

### 6. Sau khi xong
- Tự học pattern thất bại để lần sau tốt hơn
- Tạo PR GitHub tự động (nếu cấu hình `autoCreatePr: true`)
- Báo cáo chi phí LLM (USD)
- Lưu memory để resume

---

## Operator KHÔNG thể làm gì?

| Hạn chế | Lý do |
|---|---|
| Không viết code trực tiếp | Operator **điều khiển** Copilot, không tự code |
| Không chạy nếu VS Code đóng | Cần VS Code mở để giao tiếp qua CLI |
| Không đảm bảo kết quả đúng 100% | Phụ thuộc vào chất lượng Copilot trả lời |
| Không tự deploy / push remote | An toàn — không bao giờ tự push lên remote |

---

## Chạy không cần giám sát — thực tế ra sao?

**Scenario thực tế:**

```bash
# Sáng vào làm
git checkout -b feature/add-pagination
copilot-operator run \
  --goal "Add cursor-based pagination to GET /products endpoint, with tests" \
  --goal-profile feature \
  --max-iterations 8 \
  --live &   # chạy nền

# Đi họp
# ...
# Sau 15-20 phút quay lại, xem kết quả
copilot-operator status
```

**Kết quả điển hình:**
- `status: complete` — Operator tự làm xong, test pass, score ≥ 85
- `status: blocked` — Hết iterations, cần bạn xem và resume
- `status: error` — Lỗi gì đó, xem logs để biết

**Log đầy đủ** được lưu tại `.copilot-operator/logs/<run-id>/`:
- `iteration-01-prompt.md` — prompt đã gửi
- `iteration-01-response.md` — Copilot trả lời gì
- `iteration-01-decision.json` — operator quyết định gì và tại sao

---

## Benchmark — đo operator tốt đến đâu

Tạo file `benchmark.json`:

```json
{
  "name": "Project benchmark",
  "cases": [
    {
      "id": "fix-null-check",
      "goal": "Add null check for user input in login function",
      "goal_profile": "bug",
      "expected_keywords": ["null", "validation", "login"]
    }
  ]
}
```

```bash
copilot-operator benchmark --file benchmark.json
# → báo cáo pass/fail + score từng case
```

---

## Cấu hình nâng cao

### Tạo PR tự động sau khi xong

Trong `copilot-operator.yml`:

```yaml
autoCreatePr: true
```

```bash
export GITHUB_TOKEN=ghp_...
copilot-operator run --goal "..."
# → sau khi xong, tự tạo PR với description đầy đủ
```

### Chỉ định file memory cho project

Trong `.copilot-operator/repo-profile.yml`:

```yaml
repoName: my-company/my-project
summary: "REST API cho hệ thống quản lý kho"
standards:
  - Mọi endpoint phải có unit test
  - Dùng zod để validate input
priorities:
  - Coverage > 80%
  - Không có any TypeScript
protectedPaths:
  - database/migrations/
  - .env.production
```

---

## Tóm tắt nhanh

| Muốn làm gì | Lệnh |
|---|---|
| Lần đầu setup | `copilot-operator init` |
| Kiểm tra cài đúng chưa | `copilot-operator doctor` |
| Chạy fix bug | `copilot-operator run --goal "Fix ..." --goal-profile bug` |
| Chạy và theo dõi trực tiếp | thêm `--live` |
| Thử prompt không thực thi | thêm `--dry-run` |
| Tiếp tục run bị dừng | `copilot-operator resume` |
| Fix GitHub issue | `copilot-operator fix-issue --issue 42 --repo owner/repo` |
| Xem đang làm gì | `copilot-operator focus` |
| Xem toàn bộ trạng thái | `copilot-operator status` |
| Dọn logs cũ | `copilot-operator cleanup` |
