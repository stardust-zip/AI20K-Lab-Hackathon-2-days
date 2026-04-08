# SPEC — AI Product Hackathon 

- **Nhóm:** 08
- **Track:** ☐ VinFast · ☑ Vinmec · ☐ VinUni-VinSchool · ☐ XanhSM · ☐ Open
- **Problem statement (1 câu):** Bệnh nhân khi có triệu chứng đau ốm thường lúng túng không biết khám chuyên khoa nào, dẫn đến đặt nhầm khoa hoặc phải gọi tổng đài gây quá tải; AI sẽ đóng vai trò "Điều dưỡng sơ yếu", thu thập thông tin nhân khẩu học và phân tích triệu chứng ngữ nghĩa tự nhiên để gợi ý chuyên khoa, kết hợp lớp kiểm duyệt nhanh (Thin Human-Triage) để rút ngắn luồng booking mà vẫn đảm bảo an toàn y khoa tuyệt đối.

---

## 1. AI Product Canvas

| | Value | Trust | Feasibility |
|---|---|---|---|
| **Câu hỏi** | User nào? Pain gì? AI giải gì? | Khi AI sai thì sao? User sửa bằng cách nào? | Cost/latency bao nhiêu? Risk chính? |
| **Trả lời** | Bệnh nhân cần đặt lịch nhưng ngợp trước hàng chục chuyên khoa. AI phân tích text tự do để map triệu chứng ra khoa phù hợp. | AI có lớp bọc lót: Nếu độ tự tin < 85%, đẩy qua luồng "Chờ điều dưỡng duyệt nhanh". Sai tại viện: Bác sĩ điều chuyển khoa. | Dùng model cloud tốc độ cao kết hợp On-premise. Latency < 2s/turn. Risk chính: Bỏ sót Red Flags và rủi ro rò rỉ dữ liệu y tế (PHI). |

**Automation hay augmentation?** ☑ Augmentation (Tăng cường)
Justify: AI là bước đệm sơ khảo (Augmentation). Với các ca AI tự tin cao (>85%), quyền chốt lịch vẫn thuộc về user. Với các ca trung bình (60-85%), AI tạo ra một lớp "Thin Human-Triage", tóm tắt sẵn bệnh án và đề xuất khoa để Điều dưỡng thật ấn "Duyệt" (SLA 15-30s/ca) trước khi confirm cho bệnh nhân. Con số này đảm bảo điều dưỡng có đủ thời gian nhận thức ca bệnh, tránh tình trạng "duyệt mù" (rubber-stamping), đồng thời vẫn tối ưu gấp 6-12 lần so với gọi telesale (3 phút).

**Learning signal:**
1. Correction đi vào đâu? (1) Khi Điều dưỡng sửa gợi ý của AI ở luồng duyệt nhanh; (2) Khi bác sĩ khám thực tế ấn "Điều chuyển nhầm khoa". Data gồm `(Triệu chứng gốc -> Khoa đúng)` được nạp vào DB.
2. Product thu signal gì? Tỷ lệ tự book thành công tự động; Tỷ lệ Điều dưỡng phải sửa lại khoa; Tỷ lệ Bác sĩ điều chuyển nhầm khoa tại viện.
3. Data thuộc loại nào? ☐ User-specific · ☑ Domain-specific · ☐ Real-time · ☑ Human-judgment
   Có marginal value không? Có. Thông qua vòng lặp Human-in-the-loop, AI sẽ học được các thuật ngữ bệnh lý dân gian, tiếng lóng, sai chính tả của người Việt, tạo ra "con hào kinh tế" (Moat) dữ liệu độc quyền cho Vinmec.

---

## 2. User Stories — 4 paths

### Feature: Chatbot Triage (Sàng lọc triệu chứng và gợi ý chuyên khoa)

**Trigger:** User mở ứng dụng Vinmec, vào mục "Đặt lịch khám", nhập text: *“Tôi bị đau quặn bụng dưới bên phải từ tối qua, kèm ớn lạnh.”*

| Path | Câu hỏi thiết kế | Mô tả |
|---|---|---|
| Happy (AI tự tin > 85%) | User thấy gì? Flow kết thúc ra sao? | AI ngầm trích xuất Tuổi/Giới tính từ profile và hỏi thêm 1 câu chốt. AI trả kết quả: "95% phù hợp Khám Ngoại Tiêu hoá" kèm Disclaimer: *"Đây là trợ lý gợi ý chuyên khoa, không thay thế chẩn đoán y khoa..."* |
| Thin Human-Triage (AI tự tin 60-85%) | System làm gì? User trải nghiệm ra sao? | Triệu chứng mập mờ. AI báo: "Hệ thống đang tìm chuyên khoa phù hợp nhất...". Ở backend, AI đẩy tóm tắt bệnh án cho Điều dưỡng trực online. Điều dưỡng đọc nhanh đánh giá (15-30s), ấn "Duyệt: Nội Tim Mạch". User nhận kết quả. |
| Failure (Bác sĩ đổi khoa) | Recover ra sao để tránh ức chế? | Nếu AI/Điều dưỡng phân loại nhầm. Bác sĩ dùng hệ thống ấn "Nhầm khoa", bệnh nhân được CSKH dẫn trực tiếp qua khoa mới (Fast-track), ưu tiên khám ngay không xếp hàng lại. |
| Correction (Học hỏi) | Data đi vào đâu? | Nút "Sửa khoa" của Điều dưỡng online và "Nhầm khoa" của Bác sĩ kích hoạt lưu log. Cặp dữ liệu này được đưa thẳng vào Vector DB để làm giàu bộ nhớ ngữ nghĩa (Semantic Memory). |

---

## 3. Eval metrics + threshold

**Optimize precision hay recall?** ☐ Precision · ☑ Recall
Tại sao? Trong y tế, Recall là chỉ số sống còn đối với các loại bệnh nguy hiểm (Red Flags). Chấp nhận False Positive (thà AI báo động nhầm và khuyên đi Cấp Cứu) để đảm bảo tuyệt đối không bỏ lọt False Negative (nhầm nhồi máu cơ tim thành đau dạ dày thông thường).

| Metric | Threshold | Red flag (dừng khi) |
|---|---|---|
| Recall đối với nhóm Cấp Cứu (Red Flags) | > 99.5% (Tiệm cận tuyệt đối) | Có bất kỳ 1 ca cấp cứu nào bị AI phân loại là khám thông thường (Rủi ro pháp lý & tính mạng). |
| Tỉ lệ khách hoàn tất book lịch qua luồng Bot | > 60% | Tỷ lệ Drop-off (thoát ngang lúc chat) > 40% trong 2 tuần liên tiếp. |
| Tỉ lệ phản hồi "Nhầm khoa" từ Bác Sĩ tại viện | < 2% | Tỷ lệ báo "Nhầm khoa" vượt > 10% trong 1 tuần (Gây vỡ trận vận hành lâm sàng). |

---

## 4. Top 4 failure modes

| # | Trigger | Hậu quả | Mitigation |
|---|---|---|---|
| 1 | Rò rỉ dữ liệu y tế (PHI) qua API Cloud bên thứ 3 | Vi phạm quyền riêng tư, HIPAA, Nghị định 13 VN nếu gửi nguyên văn bệnh sử + nhân khẩu học qua server ngoài. | **De-identification Layer / On-premise:** Tích hợp module ẩn danh hóa (xóa Tên, SĐT, ID) trước khi gọi Cloud API, HOẶC ưu tiên deploy các mô hình Open-source (Llama-3, Qwen) on-premise tại server Vinmec. |
| 2 | Bệnh nhân dùng từ lóng, sai chính tả, hoặc cảm xúc phóng đại | Vector Search bị nhiễu do từ "sắp chết", "xây xẩm" dẫn đến báo động giả (False Positive) ồ ạt hoặc phân sai khoa. | **LLM Entity Extraction:** Dùng LLM bóc tách "Triệu chứng cốt lõi" khỏi lớp vỏ cảm xúc trước, sau đó mới đẩy vào Vector DB so khớp. |
| 3 | Quá tải hàng đợi Thin Human-Triage giờ cao điểm | Có 100 user cùng kẹt ở luồng duyệt, người bệnh chờ lâu sinh ra ức chế, thoát app. | Thiết lập Queueing SLA. Nếu quá 3 phút không có Điều dưỡng nào nhận ca, tự động bung popup "Chuyển máy gọi Tổng đài viên". |
| 4 | Prompt Injection / Ép bot kê đơn | User bắt bot kê đơn, tư vấn mẹo dân gian hoặc tự chẩn đoán bệnh. | System Prompt dùng Railguard: Persona là Điều dưỡng phân khoa. Nếu hỏi đơn thuốc, Bot chặn bằng Canned response và nhắc lại Disclaimer miễn trừ trách nhiệm. |

---

## 5. ROI 3 kịch bản

| | Conservative | Realistic | Optimistic |
|---|---|---|---|
| **Assumption** | 500 khách dùng App/ngày, 40% tương tác Bot | 1,500 khách/ngày, 65% qua luồng Bot | 3,000 khách/ngày, 85% xử lý gọn qua Bot |
| **Cost** | ~$5/ngày | ~$15/ngày | ~$30/ngày |
| **Benefit** | Lọc được các ca rõ ràng, giảm tải cho Call Center | Điều dưỡng Triage tăng x6 năng suất (từ 3 phút nghe gọi xuống 15-30s duyệt/ca) | Số hóa toàn diện. Tiết kiệm quỹ lương khổng lồ cho trực tổng đài. |
| **Net** | Trải nghiệm mới lạ, ROI dương nhẹ | Giải quyết triệt để nút thắt cổ chai ở khâu tiếp đón | Moat dữ liệu bệnh lý người Việt lớn nhất thị trường. |

**Kill criteria:** 1. Tỷ lệ Drop-off > 40% trong 2 tuần liên tiếp. 2. Tỷ lệ bác sĩ/điều dưỡng tại viện báo "Nhầm khoa" > 10% trong 1 tuần. *(Dừng để điều chỉnh UI/UX và fine-tune lại model).*

---

## 6. Mini AI spec (1 trang)

### **Tên dự án:** Vinmec AI Triage (Trợ lý Điều dưỡng Sơ yếu Thông minh)

**Vấn đề & Target User:**
Khi đau ốm, người bệnh băn khoăn không biết chọn chuyên khoa nào. Gọi tổng đài mất thời gian, tự chọn bừa dễ sai gây gián đoạn hành trình. Nút thắt cổ chai của hệ thống y tế bắt đầu ngay từ khâu booking.

**Giải pháp: Triage Augmentation, RAG & Kiến trúc Bảo mật**
Hệ thống hoạt động qua 4 lớp cốt lõi:
1. **Privacy & Entity Extraction Layer:** Text đầu vào của bệnh nhân đi qua lớp De-identification để bảo mật, sau đó LLM bóc tách thực thể lâm sàng (triệu chứng cốt lõi, tuổi, giới tính), loại bỏ nhiễu cảm xúc dài dòng.
2. **Semantic Red Flags (An toàn là số 1):** Các thực thể được nhúng (Embedding) và quét qua DB tình trạng khẩn cấp. Nếu phát hiện rủi ro, AI lập tức bung cảnh báo đỏ, hiển thị nút Gọi 115. KPI Recall > 99.5%. Giao diện BẮT BUỘC có dòng Disclaimer: *"Đây là trợ lý gợi ý chuyên khoa, không thay thế chẩn đoán y khoa..."*
3. **LLM Triage & Routing:** Với ca thông thường, AI map triệu chứng với catalog khoa, chủ động hỏi 1-2 câu để thu hẹp phạm vi.
4. **Thin Human-Triage (Lớp bọc lót vận hành):** Ca có độ tự tin < 85%, AI đóng gói bệnh án gửi màn hình cho Điều dưỡng trực online. SLAs duyệt là 15-30 giây/ca — đủ để ra quyết định lâm sàng có trách nhiệm, không bị rơi vào tình trạng "bấm bừa". Fallback 3 phút chuyển Gọi Lễ tân.

**Data Flywheel (Giải quyết Cold Start & Tạo Moat):**
Tránh việc dùng ICD-10 khô cứng làm bệnh nhân khó hiểu, Vector DB giai đoạn "Cold Start" sẽ được nạp bằng **dữ liệu Log Chat/Call Center lịch sử của chính Vinmec (đã ẩn danh)** kết hợp SNOMED CT và ICPC-2 để bám sát ngôn ngữ đời thường. 
Sau đó, hệ thống học liên tục qua: (1) Điều dưỡng sửa khoa trên app, (2) Bác sĩ báo nhầm khoa tại viện. Các bản ghi `(Triệu chứng thô dân gian -> Khoa đích chính xác)` sẽ liên tục cập nhật DB.
