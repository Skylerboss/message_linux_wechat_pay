#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OCR支付识别模块 - 当DBus通知不可靠时的备用方案
通过截图识别微信支付窗口中的收款信息
"""

import re
import time
import logging
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageGrab

    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False
    logging.warning("OpenCV/PIL not available, OCR screenshot disabled")

try:
    import pytesseract

    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False
    logging.warning("Tesseract not available, OCR text extraction disabled")

try:
    import easyocr

    HAS_EASYOCR = True
except ImportError:
    HAS_EASYOCR = False

try:
    # Python 3.8 compatibility: use backports.zoneinfo if zoneinfo not available
    import importlib.util

    spec = importlib.util.find_spec("zoneinfo")
    if spec is None:
        import sys
        from backports import zoneinfo

        sys.modules["zoneinfo"] = zoneinfo
    from paddleocr import PaddleOCR

    HAS_PADDLEOCR = True
except ImportError:
    HAS_PADDLEOCR = False

logger = logging.getLogger(__name__)


@dataclass
class OCRPaymentResult:
    """OCR识别结果"""

    amount: float
    payer: str
    timestamp: datetime
    confidence: float
    raw_text: str
    screenshot_path: Optional[str] = None
    extracted_time: Optional[str] = None  # 从OCR提取的到账时间，用于去重

    def to_dict(self) -> Dict[str, Any]:
        return {
            "amount": self.amount,
            "payer": self.payer,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "raw_text": self.raw_text,
            "screenshot_path": self.screenshot_path,
            "source": "ocr_screenshot",
        }


class WeChatOCRScanner:
    """
    微信支付OCR扫描器
    识别微信支付窗口中的收款信息
    """

    # 收款金额匹配模式 - 按优先级排序
    # 优先级：¥符号 > 收款关键词 > 通用匹配
    AMOUNT_PATTERNS = [
        r"收款金额\s*[¥￥]?\s*(\d+\.\d{2})",
        r"[¥￥]\s*(\d+\.\d{2})",  # ¥符号后跟金额（收款金额通常有这个符号）
        r"收款金额[^\d]*?(\d+\.\d{2})",  # 收款金额 X.XX
        r"收款[^\d]*?(\d+\.\d{2})[^\d]*元",  # 收款 X.XX 元
        r"收到[^\d]*?(\d+\.\d{2})",  # 收到 X.XX
        r"(\d+\.\d{2})[^\d]*元",  # X.XX 元（通用）
    ]

    STRONG_AMOUNT_PATTERNS = [
        r"收款金额\s*[¥￥]?\s*(\d+\.\d{2})",
        r"收款金额[^\d]{0,8}[¥￥]?\s*(\d+\.\d{2})",
    ]

    AMOUNT_ANCHOR_KEYWORDS = [
        "赞赏到账通知",
        "到账通知",
        "收款金额",
        "来自",
        "到账时间",
        "收到赞赏",
        "收到",
        "赞赏",
        "支付",
    ]

    # 排除这些关键词后面的金额（避免匹配到汇总金额）
    EXCLUDE_CONTEXTS = [
        r"累计金额[^\d]*\d+\.\d{2}",
        r"累计[^\d]*\d+\.\d{2}",
        r"今日收到赞赏[^\d]*\d+[^\d]*笔[^\d]*累计金额[^\d]*\d+\.\d{2}",
        r"共计[^\d]*\d+\.\d{2}",
        r"合计[^\d]*\d+\.\d{2}",
        r"共[^\d]*\d+\.\d{2}[^\d]*笔",
        r"查看详情",
        r"今日收到赞赏",
        r"支付服务",
        r"优惠",
    ]

    # 付款人匹配模式 - 支持赞赏码、转账等多种格式
    PAYER_PATTERNS = [
        r"来自\s*[:：]?\s*(\S{2,10})",  # 来自：如愿以偿（2-10个字符）
        r"来自\s+(\S{2,10})",  # 来自 如愿以偿
        r"(\S{2,10})\s*向你付款",
        r"付款人[：:]\s*(\S{2,10})",
        r"(\S{2,10})的转账",
        r"转账人[：:]\s*(\S{2,10})",
        r"(\S{2,10})转账",
    ]

    # 到账时间匹配模式
    TIME_PATTERNS = [
        r"到账时间[:：]?\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",  # 到账时间: 2026-03-29 22:19:19
        r"到账时间[:：]?\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})",  # 到账时间: 2026-03-29 22:19
        r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",  # 通用时间格式
        r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})",  # 通用时间格式（无秒）
    ]

    PAYER_STOP_WORDS = {
        "微信支付",
        "weixin",
        "pay",
        "weixinpay",
        "赞赏到账通知",
        "到账通知",
        "今日收到赞赏",
        "支付服务",
        "查看详情",
        "择优优惠",
        "二维码收款",
        "收款金额",
        "到账时间",
        "累计金额",
        "message",
        "messages",
    }

    # 微信窗口标题特征
    WINDOW_TITLES = [
        "微信支付",
        "微信收款",
        "微信",
        "WeChat",
        "支付",
        "收款",
        "二维码收款",
    ]

    def __init__(
        self,
        ocr_engine: str = "auto",
        lang: str = "chi_sim+eng",
        confidence_threshold: float = 0.6,
    ):
        """
        初始化OCR扫描器

        Args:
            ocr_engine: OCR引擎选择 ("tesseract", "easyocr", "auto")
            lang: OCR语言设置
            confidence_threshold: 置信度阈值
        """
        self.ocr_engine = ocr_engine
        self.lang = lang
        self.confidence_threshold = confidence_threshold
        self._easyocr_reader: Any = None
        self._paddleocr_reader: Any = None

        # 检查可用性
        if not HAS_CV2:
            raise RuntimeError("OpenCV/PIL required for OCR screenshot")

        if ocr_engine == "auto":
            if HAS_PADDLEOCR:
                self.ocr_engine = "paddleocr"
            elif HAS_EASYOCR:
                self.ocr_engine = "easyocr"
            elif HAS_TESSERACT:
                self.ocr_engine = "tesseract"
            else:
                raise RuntimeError(
                    "No OCR engine available (install paddleocr, pytesseract or easyocr)"
                )

        if self.ocr_engine == "paddleocr" and not HAS_PADDLEOCR:
            raise RuntimeError("PaddleOCR not available")
        if self.ocr_engine == "easyocr" and not HAS_EASYOCR:
            raise RuntimeError("EasyOCR not available")
        if self.ocr_engine == "tesseract" and not HAS_TESSERACT:
            raise RuntimeError("Tesseract not available")

        logger.info(f"OCR Scanner initialized with engine: {self.ocr_engine}")

    def _get_paddleocr_reader(self):
        """获取/创建PaddleOCR reader实例"""
        if self._paddleocr_reader is None and HAS_PADDLEOCR:
            # 使用中文模型，简化的参数配置
            self._paddleocr_reader = PaddleOCR(lang="ch")
        return self._paddleocr_reader

    def _extract_text_paddleocr(self, image: np.ndarray) -> Tuple[str, List[Tuple]]:
        """使用PaddleOCR提取文字"""
        try:
            reader = self._get_paddleocr_reader()
            # PaddleOCR需要保存为临时文件
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
                cv2.imwrite(tmp_path, image)

            results = reader.ocr(tmp_path, cls=True)

            # 清理临时文件
            import os

            os.unlink(tmp_path)

            # 提取文本和置信度
            texts = []
            details = []
            if results and len(results) > 0 and results[0]:
                for line in results[0]:
                    if line:
                        bbox, (text, conf) = line
                        texts.append(text)
                        details.append((text, conf, bbox))

            return " ".join(texts), details
        except Exception as e:
            logger.error(f"PaddleOCR failed: {e}")
            return "", []

    def _get_easyocr_reader(self):
        """获取/创建EasyOCR reader实例"""
        if self._easyocr_reader is None and HAS_EASYOCR:
            lang_codes = ["ch_sim", "en"] if "chi" in self.lang else ["en"]
            self._easyocr_reader = easyocr.Reader(lang_codes)
        return self._easyocr_reader

    def capture_screen(
        self, region: Optional[Tuple[int, int, int, int]] = None
    ) -> Optional[np.ndarray]:
        """
        截取屏幕或指定区域

        Args:
            region: (x, y, width, height) 指定区域

        Returns:
            OpenCV格式的图像数组
        """
        try:
            if region:
                x, y, w, h = region
                screenshot = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            else:
                screenshot = ImageGrab.grab()

            # 转换为OpenCV格式
            screenshot = cv2.cvtColor(np.array(screenshot), cv2.COLOR_RGB2BGR)
            return screenshot

        except Exception as e:
            logger.error(f"Screenshot capture failed: {e}")
            return None

    def _extract_text_tesseract(self, image: np.ndarray) -> str:
        """使用Tesseract提取文字"""
        try:
            # 预处理图像以提高识别率
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            # 二值化
            _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)

            # 配置Tesseract
            config = f"--oem 3 --psm 6 -l {self.lang}"
            text = pytesseract.image_to_string(binary, config=config)

            return text
        except Exception as e:
            logger.error(f"Tesseract OCR failed: {e}")
            return ""

    def _extract_text_easyocr(self, image: np.ndarray) -> Tuple[str, List[Tuple]]:
        """使用EasyOCR提取文字"""
        try:
            reader = self._get_easyocr_reader()
            results = reader.readtext(image)

            # 提取文本和置信度
            texts = []
            details = []
            for bbox, text, conf in results:
                texts.append(text)
                details.append((text, conf, bbox))

            return " ".join(texts), details
        except Exception as e:
            logger.error(f"EasyOCR failed: {e}")
            return "", []

    def _detect_wechat_payment_window(
        self, image: np.ndarray
    ) -> Optional[Tuple[int, int, int, int]]:
        """
        检测微信支付窗口位置
        使用模板匹配或颜色特征
        """
        # TODO: 实现窗口检测算法
        # 目前返回全屏
        h, w = image.shape[:2]
        return (0, 0, w, h)

    def _extract_payment_info(
        self, text: str, ocr_details: Optional[List[Tuple]] = None
    ) -> Optional[OCRPaymentResult]:
        """
        从OCR文本中提取支付信息
        """
        text = text.replace("\n", " ").strip()

        # 检查是否包含支付相关关键词
        if not any(
            kw in text
            for kw in ["收款", "支付", "转账", "收到", "元", "￥", "$", "赞赏"]
        ):
            return None

        # 提取金额 - 基于锚点和上下文打分，避免误取历史统计/详情金额
        amount = None
        all_matches = []
        lowered_text = text.lower()

        for pattern in self.STRONG_AMOUNT_PATTERNS:
            strong_match = re.search(pattern, text)
            if not strong_match:
                continue

            try:
                strong_amount = float(strong_match.group(1))
            except ValueError:
                continue

            if 0 < strong_amount < 100000:
                amount = strong_amount
                logger.debug(
                    "OCR strong amount match selected: amount=%s pattern=%s",
                    strong_amount,
                    pattern,
                )
                break

        if amount is not None:
            payer = self._extract_payer(text)

            extracted_time = None
            for pattern in self.TIME_PATTERNS:
                match = re.search(pattern, text)
                if match:
                    extracted_time = match.group(1)
                    break

            confidence = 0.88
            if ocr_details:
                avg_conf = (
                    sum(d[1] for d in ocr_details) / len(ocr_details)
                    if ocr_details
                    else 0
                )
                confidence = min(0.97, 0.6 + avg_conf * 0.4)

            return OCRPaymentResult(
                amount=amount,
                payer=payer,
                timestamp=datetime.now(),
                confidence=confidence,
                raw_text=text,
                extracted_time=extracted_time,
            )

        for pattern in self.AMOUNT_PATTERNS:
            for match in re.finditer(pattern, text):
                try:
                    matched_amount = float(match.group(1))
                    if 0 < matched_amount < 100000:
                        match_start = max(0, match.start() - 24)
                        match_end = min(len(text), match.end() + 24)
                        context = text[match_start:match_end]
                        context_lower = lowered_text[match_start:match_end]
                        global_context_start = max(0, match.start() - 48)
                        global_context_end = min(len(text), match.end() + 48)
                        global_context = text[global_context_start:global_context_end]

                        is_excluded = any(
                            re.search(exclude, context)
                            or re.search(exclude, global_context)
                            for exclude in self.EXCLUDE_CONTEXTS
                        )
                        if not is_excluded:
                            score = 0
                            if pattern == self.AMOUNT_PATTERNS[0]:
                                score += 5
                            if any(
                                keyword in context or keyword.lower() in context_lower
                                for keyword in self.AMOUNT_ANCHOR_KEYWORDS
                            ):
                                score += 4

                            absolute_prefix = text[
                                max(0, match.start() - 8) : match.start()
                            ]
                            if "¥" in absolute_prefix or "￥" in absolute_prefix:
                                score += 3

                            if matched_amount.is_integer():
                                score += 1

                            all_matches.append(
                                {
                                    "amount": matched_amount,
                                    "position": match.start(),
                                    "pattern": pattern,
                                    "context": context,
                                    "score": score,
                                }
                            )
                except ValueError:
                    continue

        # 优先选择高分候选；同分时优先更靠前的金额
        if all_matches:
            best_match = sorted(
                all_matches,
                key=lambda item: (-item["score"], item["position"]),
            )[0]
            amount = best_match["amount"]
            logger.debug(
                "OCR amount candidates: %s | selected=%s",
                [
                    {
                        "amount": item["amount"],
                        "score": item["score"],
                        "context": item["context"],
                    }
                    for item in all_matches[:5]
                ],
                best_match,
            )

        if amount is None:
            return None

        # 提取付款人
        payer = self._extract_payer(text)

        # 提取到账时间（用于去重）
        extracted_time = None
        for pattern in self.TIME_PATTERNS:
            match = re.search(pattern, text)
            if match:
                extracted_time = match.group(1)
                break

        # 计算置信度
        confidence = 0.8
        if ocr_details:
            avg_conf = (
                sum(d[1] for d in ocr_details) / len(ocr_details) if ocr_details else 0
            )
            confidence = min(0.95, 0.5 + avg_conf * 0.5)

        return OCRPaymentResult(
            amount=amount,
            payer=payer,
            timestamp=datetime.now(),
            confidence=confidence,
            raw_text=text,
            extracted_time=extracted_time,
        )

    def _extract_payer(self, text: str) -> str:
        """从OCR文本中提取付款人，优先规则匹配，失败时再做邻域兜底"""
        for pattern in self.PAYER_PATTERNS:
            match = re.search(pattern, text)
            if not match:
                continue

            candidate = self._clean_payer_candidate(match.group(1))
            if candidate:
                return candidate

        fallback_payer = self._extract_payer_from_context(text)
        if fallback_payer:
            return fallback_payer

        return "未知"

    def _clean_payer_candidate(self, candidate: str) -> Optional[str]:
        """清洗付款人候选值"""
        if not candidate:
            return None

        cleaned = re.sub(r"[^\w\u4e00-\u9fff]", "", candidate, flags=re.UNICODE).strip()
        if len(cleaned) < 2 or len(cleaned) > 12:
            return None

        lowered = cleaned.lower()
        lowered_stop_words = {word.lower() for word in self.PAYER_STOP_WORDS}
        if any(stop_word in lowered for stop_word in lowered_stop_words):
            return None

        if re.fullmatch(r"\d+", cleaned):
            return None

        return cleaned

    def _extract_payer_from_context(self, text: str) -> Optional[str]:
        """在'赞赏/来自/付款'附近寻找付款人"""
        compact_text = re.sub(r"\s+", " ", text)
        context_patterns = [
            r"赞赏(?:到账通知)?\s*([\u4e00-\u9fffA-Za-z0-9]{2,12})",
            r"来自\s*([\u4e00-\u9fffA-Za-z0-9]{2,12})",
            r"付款(?:人)?\s*([\u4e00-\u9fffA-Za-z0-9]{2,12})",
        ]

        for pattern in context_patterns:
            match = re.search(pattern, compact_text)
            if not match:
                continue

            candidate = self._clean_payer_candidate(match.group(1))
            if candidate:
                return candidate

        chinese_candidates = re.findall(r"[\u4e00-\u9fff]{2,8}", compact_text)
        for candidate in chinese_candidates:
            cleaned = self._clean_payer_candidate(candidate)
            if cleaned:
                return cleaned

        english_candidates = re.findall(r"[A-Za-z][A-Za-z0-9_]{1,11}", compact_text)
        for candidate in english_candidates:
            cleaned = self._clean_payer_candidate(candidate)
            if cleaned:
                return cleaned

        return None

    def scan(
        self, region: Optional[Tuple[int, int, int, int]] = None
    ) -> Optional[OCRPaymentResult]:
        """
        扫描屏幕识别支付信息

        Args:
            region: 指定扫描区域 (x, y, width, height)，None则扫描全屏

        Returns:
            识别结果或None
        """
        # 截图
        image = self.capture_screen(region)
        if image is None:
            return None

        # OCR识别
        if self.ocr_engine == "paddleocr":
            text, details = self._extract_text_paddleocr(image)
            result = self._extract_payment_info(text, details)
        elif self.ocr_engine == "easyocr":
            text, details = self._extract_text_easyocr(image)
            result = self._extract_payment_info(text, details)
        else:
            text = self._extract_text_tesseract(image)
            result = self._extract_payment_info(text)

        if result and result.confidence >= self.confidence_threshold:
            logger.info(
                f"[OCR DETECTED] {result.amount}元 from {result.payer} (confidence: {result.confidence:.2f})"
            )
            return result

        return None

    def scan_continuous(
        self,
        interval: float = 2.0,
        callback=None,
        max_duration: Optional[float] = None,
        region: Optional[Tuple[int, int, int, int]] = None,
    ):
        """
        持续扫描屏幕

        Args:
            interval: 扫描间隔（秒）
            callback: 检测到支付时的回调函数
            max_duration: 最大运行时间（秒），None则一直运行
            region: 指定扫描区域 (x, y, width, height)
        """
        start_time = time.time()
        scan_count = 0

        logger.info(
            f"Starting continuous OCR scan (interval: {interval}s, region: {region or 'full-screen'})"
        )

        try:
            while True:
                scan_count += 1
                result = self.scan(region=region)

                if result and callback:
                    callback(result)

                # 检查运行时间
                if max_duration and (time.time() - start_time) > max_duration:
                    logger.info(
                        f"Max duration reached, stopping scan after {scan_count} scans"
                    )
                    break

                time.sleep(interval)

        except KeyboardInterrupt:
            logger.info(f"OCR scan stopped after {scan_count} scans")


class WindowAutoLocator:
    """
    窗口自动定位器
    自动查找微信支付窗口位置
    """

    def __init__(self):
        self.window_positions = {}

    def find_wechat_window(self) -> Optional[Tuple[int, int, int, int]]:
        """
        查找微信窗口位置
        使用平台特定的窗口管理API
        """
        # Linux: 使用X11或Wayland API
        # TODO: 实现窗口查找
        return None

    def get_wechat_payment_region(self) -> Optional[Tuple[int, int, int, int]]:
        """获取微信支付通知区域位置"""
        # 通常支付通知显示在屏幕角落
        # 可以根据分辨率自适应
        return None


if __name__ == "__main__":
    # 测试代码
    logging.basicConfig(level=logging.DEBUG)

    try:
        scanner = WeChatOCRScanner(ocr_engine="auto")

        print("启动OCR扫描测试...")
        print("请确保屏幕上有微信支付窗口")
        print("按 Ctrl+C 停止")

        def on_payment(result):
            print(f"\n=== OCR识别到支付 ===")
            print(f"金额: {result.amount} 元")
            print(f"付款人: {result.payer}")
            print(f"置信度: {result.confidence:.2f}")
            print(f"原始文本: {result.raw_text[:100]}...")
            print("================\n")

        # 单次扫描测试
        result = scanner.scan()
        if result:
            on_payment(result)
        else:
            print("未识别到支付信息")

    except Exception as e:
        print(f"OCR测试失败: {e}")
        print("请确保已安装依赖: pip install opencv-python pillow pytesseract easyocr")
